# -*- coding: utf-8 -*-
"""在庫管理ダッシュボードの集計（画面とは切り離した純粋なロジック）。

元データ（出庫として数えるもの）
- 請求先（取引先マスタに登録のあるお客様）… **発行済みの領収書**を数える。
    数量(kg)は、その領収書に紐づく請求書の数量。領収書のない請求書は、**確定済み**のものだけ
    設定で数える（下書き・注文は数えない。同じ納品を二重に数えないため）。
- それ以外のお客様（消費者向けの注文）… **出荷確定した注文**（発送済み・手渡し完了）を数える。
    同じ伝票番号の注文は1件として数える（LINE入力の注文とヤマト履歴の取込行が
    同じ出荷を二重に持っていることがあるため）。
- 入庫・棚卸・手修正 … 食糧法の帳簿と共通の ledger_entries
    （棚卸＝その場所・品種・形態の実数。以後の入出庫を足し引きして現在庫を出す）

在庫は「形態（玄米/精米）× 品種 × 保管場所」ごとに持つ。
月の使える量は、玄米を白米（精米）に換算（歩留まり）して全体で見る。
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from . import db, ledger

JST = timezone(timedelta(hours=9))
CONFIG_KEY = "stock_config"
UNSET_LOC = "（場所未指定）"
FORMS = ledger.FORMS  # ("玄米", "精米")

_DEFAULT_CFG = {
    "locations": [{"name": "冷蔵庫", "capacity_kg": 0.0},
                  {"name": "倉庫", "capacity_kg": 0.0}],
    "default_location": {"精米": "冷蔵庫", "玄米": "倉庫"},   # 出庫（注文・請求書）を引く保管場所
    "variety_location": {},               # 品種ごとの上書き {品種: {"玄米": 場所, "精米": 場所}}
    "default_variety": "コシヒカリ",
    "varieties": ["コシヒカリ", "ひとめぼれ", "天のつぶ", "ミルキークイーン", "あきたこまち"],
    "count_sent_invoices": True,          # 領収書のない請求先は、確定済みの請求書で数える
    "client_monthly_kg": {},              # 卸先ごとの月の使用量（kg・白米換算）{請求先ID: kg}
    "milling_yield": 0.9,                 # 玄米→白米（精米）の歩留まり（玄米1kgで白米0.9kg）
    "harvest_month": 9,                   # 次の新米が入る月。ここまでの月数で「月の使える量」を割る
    "risk_high_months": 1.5,              # これ未満で欠品リスク「高」
    "risk_mid_months": 3.0,               # これ未満で「中」
    "stale_count_days": 45,               # 棚卸がこれより古いと注意
    "excluded_out": [],                   # 手動で在庫計算から外した出庫行のID
}


def today() -> date:
    return datetime.now(JST).date()


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
def load_config() -> dict:
    saved = db.get_setting(CONFIG_KEY) or {}
    cfg = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
           for k, v in _DEFAULT_CFG.items()}
    cfg.update({k: v for k, v in saved.items() if k in _DEFAULT_CFG})
    return cfg


def save_config(cfg: dict) -> None:
    db.set_setting(CONFIG_KEY, {k: cfg.get(k, _DEFAULT_CFG[k]) for k in _DEFAULT_CFG})


def location_names(cfg: dict) -> list[str]:
    return [l["name"] for l in cfg["locations"] if l.get("name")]


def detect_variety(text: str, cfg: dict) -> str:
    """商品名・品目名から品種を拾う。分からなければ既定の品種。"""
    for v in cfg["varieties"]:
        if v and v in (text or ""):
            return v
    return cfg["default_variety"]


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _clean_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("　", " ")).strip()


# ---------------------------------------------------------------------------
# 出庫・確保の組み立て
# ---------------------------------------------------------------------------
def _order_date(o: dict) -> date | None:
    return ledger.parse_date(o.get("ship_date")) or ledger.parse_date(o.get("order_date")) \
        or ledger.parse_date(o.get("created_at"))


def _default_loc(form: str, cfg: dict, variety: str | None = None) -> str:
    """出庫を引く保管場所。品種ごとの指定があればそれ、無ければ形態ごとの既定。"""
    if variety:
        v = ((cfg.get("variety_location") or {}).get(variety) or {}).get(form)
        if v:
            return v
    return (cfg.get("default_location") or {}).get(form) or UNSET_LOC


def _client_price_per_kg(c: dict) -> float:
    try:
        price = float(c.get("price_per_5kg") or 0)
        unit = float(c.get("unit_kg") or 5.0) or 5.0
        return price / unit
    except (TypeError, ValueError):
        return 0.0


def _receipt_invoice(v: dict, pendings: dict) -> dict | None:
    """領収書に対応する請求書（数量の元）。紐づけが無い古い領収書は、同じ請求先・同じ発行日から探す。"""
    p = pendings.get(v.get("invoice_key") or "")
    if p:
        return p
    cands = [q for q in pendings.values()
             if q.get("client_id") == v.get("client_id") and q.get("issue_date") == v.get("issue_date")]
    if not cands:
        return None
    amt = _f(v.get("amount"))
    return min(cands, key=lambda q: abs(_f(q.get("amount")) - amt))


def build_movements(orders, pendings, clients, cfg, flags, receipts=None):
    """出庫行（在庫から出たもの）と確保行（未出荷の注文）を作る。

    - 請求先のお客様：発行済みの領収書だけを数える（注文・請求書は数えない）
    - それ以外（消費者向け）：出荷確定した注文を数える

    returns (out_rows, reserved_rows)
    out_rows の各要素: id / date / customer_key / customer / source / variety /
                       form / kg / location / dup / excluded / skip / note
    skip=True は「数えない行」（請求先の注文など。領収書で数えるため）。
    """
    receipts = receipts or {}
    excluded = set(cfg.get("excluded_out") or [])
    out: list[dict] = []
    reserved: list[dict] = []
    cl_by_id = {c["id"]: c for c in clients}
    b2b_ids = {int(c["customer_id"]): c for c in clients if c.get("customer_id")}

    shipped = [o for o in orders if o.get("status") == "shipped"]
    shipped.sort(key=lambda o: (_order_date(o) or date.min, int(o.get("id") or 0)))
    seen_tracking: set[tuple] = set()
    for o in shipped:
        d = _order_date(o)
        if d is None:
            continue
        parts, _ = ledger.split_order(o, flags)
        if not parts:
            continue
        cid = int(o.get("customer_id") or 0)
        trk = (o.get("tracking_no") or "").strip()
        dup = False
        if trk:
            k = (cid, trk)
            dup = k in seen_tracking
            seen_tracking.add(k)
        variety = detect_variety(o.get("product_name") or "", cfg)
        cname = _clean_name(o.get("customer_name") or "")
        b2b = cid in b2b_ids
        for (rt, fm), kg in parts.items():
            rid = f'o{int(o["id"])}:{fm}'
            out.append({
                "id": rid, "date": d, "customer_key": f"c{cid}",
                "customer": cname, "customer_id": cid,
                "source": "注文", "variety": variety, "form": fm, "kg": float(kg),
                "location": _default_loc(fm, cfg, variety), "dup": dup,
                "excluded": rid in excluded, "skip": b2b,
                "note": ("請求先のため領収書で数えます（注文は数えません）" if b2b
                         else "同じ伝票番号の注文と重複" if dup else ""),
                "product": o.get("product_name") or "",
                "b2b_client": b2b_ids[cid]["id"] if b2b else "",
            })

    for o in orders:
        if o.get("status") == "shipped":
            continue
        parts, _ = ledger.split_order(o, flags)
        variety = detect_variety(o.get("product_name") or "", cfg)
        for (rt, fm), kg in parts.items():
            reserved.append({"id": f'o{int(o["id"])}:{fm}', "form": fm, "kg": float(kg),
                             "variety": variety, "location": _default_loc(fm, cfg, variety),
                             "customer": _clean_name(o.get("customer_name") or "")})

    # 請求先：発行済みの領収書（数量は紐づく請求書の数量）
    for rid, v in receipts.items():
        d = ledger.parse_date(v.get("issue_date"))
        c = cl_by_id.get(v.get("client_id"))
        if d is None or c is None:
            continue
        inv = _receipt_invoice(v, pendings)
        item = c.get("item_desc") or ""
        note = "発行済みの領収書"
        if inv and _f(inv.get("total_kg")) > 0:
            kg = _f(inv.get("total_kg"))
            inv_amt, r_amt = _f(inv.get("amount")), _f(v.get("amount"))
            if inv_amt > 0 and r_amt > 0 and abs(r_amt - inv_amt) / inv_amt > 0.1:
                kg = kg * r_amt / inv_amt       # 一部だけの領収書は、金額の割合で数量を按分
                note += "（請求書と金額が違うため按分）"
        else:
            pk = _client_price_per_kg(c)
            kg = (_f(v.get("amount")) / pk) if pk > 0 else 0.0
            note += "（請求書が見つからないため金額から概算）" if kg else "（数量が分からないため0kg）"
        cname = _clean_name(c.get("name") or "").rstrip("様").strip()
        cid = int(c.get("customer_id") or 0)
        form = "玄米" if ("玄米" in item and "精米" not in item) else "精米"
        variety = detect_variety(item, cfg)
        out.append({
            "id": f"r{rid}", "date": d, "customer_key": f"b:{c['id']}",
            "customer": cname, "customer_id": cid, "source": "領収書",
            "variety": variety, "form": form, "kg": kg,
            "location": _default_loc(form, cfg, variety), "dup": False,
            "excluded": f"r{rid}" in excluded, "skip": False, "note": note,
            "product": item, "b2b_client": c["id"],
        })
    # 領収書を発行していない請求先：確定済み（送信・確定した）請求書で数える
    if cfg.get("count_sent_invoices", True):
        rec_keys = {v.get("invoice_key") for v in receipts.values() if v.get("invoice_key")}
        rec_pairs = {(v.get("client_id"), v.get("issue_date")) for v in receipts.values()
                     if not v.get("invoice_key")}
        for k, p in pendings.items():
            if p.get("status") != "sent" or k in rec_keys                     or (p.get("client_id"), p.get("issue_date")) in rec_pairs:
                continue
            c = cl_by_id.get(p.get("client_id"))
            d = ledger.parse_date(p.get("issue_date"))
            kg = _f(p.get("total_kg"))
            if c is None or d is None or kg <= 0:
                continue
            item = c.get("item_desc") or ""
            form = "玄米" if ("玄米" in item and "精米" not in item) else "精米"
            variety = detect_variety(item, cfg)
            cname = _clean_name(c.get("name") or "").rstrip("様").strip()
            cid = int(c.get("customer_id") or 0)
            out.append({
                "id": f"i{k}", "date": d, "customer_key": f"b:{c['id']}",
                "customer": cname, "customer_id": cid, "source": "請求書",
                "variety": variety, "form": form, "kg": kg,
                "location": _default_loc(form, cfg, variety), "dup": False,
                "excluded": f"i{k}" in excluded, "skip": False,
                "note": "確定済みの請求書（領収書は未発行）", "product": item, "b2b_client": c["id"],
            })
    out.sort(key=lambda r: (r["date"], r["id"]))
    return out, reserved


def load_out_rows() -> list[dict]:
    """今のデータから全出庫行を作る（画面から月末在庫などを計算するための補助）。"""
    from . import billing
    out, _ = build_movements(db.list_orders(), billing.get_pendings(), billing.get_clients(),
                             load_config(), ledger.load_flags(),
                             db.get_setting(billing.RECEIPTS_KEY) or {})
    return out


def counted(rows):
    """在庫計算に使う出庫行（重複・手動除外・数えない行・0kgを除く）。"""
    return [r for r in rows if not r["dup"] and not r["excluded"] and not r.get("skip") and r["kg"] > 0]


# ---------------------------------------------------------------------------
# 在庫（形態×品種×保管場所）
# ---------------------------------------------------------------------------
def _entry_key(e: dict, cfg: dict) -> tuple[str, str, str]:
    fm = e.get("form") if e.get("form") in FORMS else "玄米"
    var = e.get("variety") or cfg["default_variety"]
    loc = e.get("location") or UNSET_LOC
    return fm, var, loc


def compute_stock(entries, out_rows, cfg, as_of: date) -> dict[tuple, float]:
    """as_of 時点の在庫kg。{(形態, 品種, 保管場所): kg}

    その組み合わせの直近（as_of以前）の棚卸を基準に、それ以後の入庫・出庫を足し引きする。
    as_of より前に棚卸が無く、後に棚卸がある組み合わせは、その棚卸の実数から
    間の入出庫を逆算して出す（過去の月末在庫を見たり直したりできるように）。
    棚卸がまったく無い組み合わせは、記録された入庫から出庫を引いた値になる（0起点）。
    """
    counts: dict[tuple, list[tuple[date, float]]] = defaultdict(list)
    for e in entries:
        if e.get("kind") != "stock":
            continue
        d = ledger.parse_date(e.get("entry_date"))
        if d is None:
            continue
        counts[_entry_key(e, cfg)].append((d, _f(e.get("qty_kg"))))
    for lst in counts.values():
        lst.sort(key=lambda t: t[0])   # 同じ日付は後に登録したものが優先（安定ソート）

    events: list[tuple[date, tuple, float]] = []
    for e in entries:
        d = ledger.parse_date(e.get("entry_date"))
        kind = e.get("kind")
        if d is None or kind == "stock":
            continue
        k = _entry_key(e, cfg)
        if kind in ("buy", "produce", "adjust"):
            events.append((d, k, _f(e.get("qty_kg"))))
        elif kind == "move":
            to = e.get("to_location") or UNSET_LOC
            events.append((d, k, -_f(e.get("qty_kg"))))
            events.append((d, (k[0], k[1], to), _f(e.get("qty_kg"))))
        elif kind == "mill":
            events.append((d, ("玄米", k[1], k[2]), -_f(e.get("qty_kg"))))
            events.append((d, ("精米", k[1], _default_loc("精米", cfg, k[1])), _f(e.get("qty_out_kg"))))
    for r in counted(out_rows):
        events.append((r["date"], (r["form"], r["variety"], r["location"]), -r["kg"]))

    by_key: dict[tuple, list[tuple[date, float]]] = defaultdict(list)
    for d, k, delta in events:
        by_key[k].append((d, delta))

    stock: dict[tuple, float] = {}
    has_count: set[tuple] = set()
    for k, lst in counts.items():
        before = [t for t in lst if t[0] <= as_of]
        if before:
            bd, q = before[-1]
            stock[k] = q + sum(dl for d, dl in by_key.get(k, ()) if bd < d <= as_of)
            has_count.add(k)
        else:
            ad, q = lst[0]          # as_of より後の最初の棚卸から逆算
            stock[k] = q - sum(dl for d, dl in by_key.get(k, ()) if as_of < d <= ad)
            has_count.add(k)
    for k, lst in by_key.items():
        if k in counts:
            continue
        stock[k] = sum(dl for d, dl in lst if d <= as_of)
    return {k: round(v, 2) for k, v in stock.items() if abs(v) > 1e-9 or k in has_count}


def _month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def range_months(start: date, end: date, limit: int = 36) -> list[str]:
    """start〜end にかかる月（'YYYY-MM'）を古い順に。最大 limit か月。"""
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month) and len(out) < limit:
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def crop_year_of(d: date) -> int:
    """その日が属する米の年度（10月始まり＝新米の入る前の9月末まで）。2027-09-30 → 2026。"""
    return d.year if d.month >= 10 else d.year - 1


def crop_year_range(y: int) -> tuple[date, date]:
    return date(y, 10, 1), date(y + 1, 9, 30)


def fy_months(fy: int) -> list[str]:
    """在庫画面の1年間の月（10月〜翌9月）。fy は10月が属する年。"""
    return [f"{fy}-{m:02d}" if m >= 10 else f"{fy + 1}-{m:02d}" for m in (10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9)]


def _month_end(ym: str) -> date:
    y, m = int(ym[:4]), int(ym[5:])
    nxt = date(y + (m == 12), (m % 12) + 1, 1)
    return nxt - timedelta(days=1)


def _prev_ym(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _prev_months(ym: str, n: int) -> list[str]:
    """ym の前の n か月（古い順）。"""
    y, m = int(ym[:4]), int(ym[5:])
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}-{m:02d}")
    return sorted(out)


def _period_text(d: date) -> str:
    part = "上旬" if d.day <= 10 else "中旬" if d.day <= 20 else "下旬"
    return f"{d.month}月{part}"


# ---------------------------------------------------------------------------
# ダッシュボード本体
# ---------------------------------------------------------------------------
def build(fy: int | None = None, *, start: date | None = None, end: date | None = None,
          variety: str | None = None, location: str | None = None,
          form: str | None = None, now: date | None = None,
          orders=None, entries=None, pendings=None, clients=None, receipts=None) -> dict:
    now = now or today()
    cfg = load_config()
    flags = ledger.load_flags()
    if orders is None:
        orders = db.list_orders()
    if entries is None:
        entries = db.list_ledger_entries()
    if pendings is None or clients is None:
        from . import billing
        pendings = billing.get_pendings() if pendings is None else pendings
        clients = billing.get_clients() if clients is None else clients

    if receipts is None:
        from . import billing
        receipts = db.get_setting(billing.RECEIPTS_KEY) or {}

    all_out, reserved = build_movements(orders, pendings, clients, cfg, flags, receipts)

    def keep(r):
        return ((variety in (None, "すべて") or r["variety"] == variety)
                and (location in (None, "すべて") or r["location"] == location)
                and (form in (None, "すべて") or r["form"] == form))

    out_rows = [r for r in all_out if keep(r)]
    live_out = counted(out_rows)
    res_rows = [r for r in reserved if keep(r)]

    # --- 在庫 ---
    stock_all = compute_stock(entries, all_out, cfg, now)
    stock_rows = [{"form": k[0], "variety": k[1], "location": k[2], "kg": v}
                  for k, v in sorted(stock_all.items()) if keep({"form": k[0], "variety": k[1], "location": k[2]})]
    total = sum(r["kg"] for r in stock_rows)
    by_form = {f: sum(r["kg"] for r in stock_rows if r["form"] == f) for f in FORMS}
    by_loc: dict[str, dict] = {}
    for r in stock_rows:
        d = by_loc.setdefault(r["location"], {"total": 0.0, "玄米": 0.0, "精米": 0.0})
        d["total"] += r["kg"]
        d[r["form"]] += r["kg"]
    by_var: dict[str, float] = defaultdict(float)
    for r in stock_rows:
        by_var[r["variety"]] += r["kg"]

    has_counts = any(e.get("kind") == "stock" for e in entries)
    has_base = has_counts or any(e.get("kind") in ("buy", "produce", "adjust", "mill") for e in entries)
    last_count = max((ledger.parse_date(e.get("entry_date")) for e in entries
                      if e.get("kind") == "stock" and ledger.parse_date(e.get("entry_date"))),
                     default=None)

    reserved_kg = sum(r["kg"] for r in res_rows)
    reserved_by_form = {f: sum(r["kg"] for r in res_rows if r["form"] == f) for f in FORMS}
    available = total - reserved_kg

    # --- 出庫の月次 ---
    # 対象期間：米の年度（10月〜翌9月）または、開始日・終了日の自由な期間（月単位で表示）
    if start and end:
        months = range_months(start, end)
        label = f"{start:%Y/%m/%d}〜{end:%Y/%m/%d}"
    else:
        fy = fy if fy is not None else crop_year_of(now)
        months = fy_months(fy)
        label = f"{fy}年産（{fy}/10〜{fy + 1}/9）"
    cur_ym = _month_key(now)
    out_month: dict[str, float] = defaultdict(float)
    out_month_form: dict[str, dict] = defaultdict(lambda: {"玄米": 0.0, "精米": 0.0})
    for r in live_out:
        ym = _month_key(r["date"])
        out_month[ym] += r["kg"]
        out_month_form[ym][r["form"]] += r["kg"]

    # 直近3か月（今月の前の3か月）と、その前の3か月
    span = _prev_months(cur_ym, 3)
    prev_span = _prev_months(_prev_months(cur_ym, 3)[0], 3)
    if any(out_month.get(m, 0.0) > 0 for m in out_month if m < cur_ym):
        monthly_avg = sum(out_month.get(m, 0.0) for m in span) / 3.0
    else:
        # 今月より前の実績が無ければ、今月これまでの分を月ペースとみなす
        monthly_avg = out_month.get(cur_ym, 0.0)

    # --- 予測 ---
    months_left = (total / monthly_avg) if monthly_avg > 0 and total > 0 else None
    run_out = (now + timedelta(days=months_left * 30.44)) if months_left is not None else None
    risk = None
    if has_base and monthly_avg > 0:
        if total <= 0:
            risk = "高"
        elif months_left < cfg["risk_high_months"]:
            risk = "高"
        elif months_left < cfg["risk_mid_months"]:
            risk = "中"
        else:
            risk = "低"

    # 今月の出庫（前月の同じ日数までと比べる）
    prev_ym = _prev_ym(cur_ym)
    mtd = out_month.get(cur_ym, 0.0)
    prev_same = sum(r["kg"] for r in live_out
                    if _month_key(r["date"]) == prev_ym and r["date"].day <= now.day)
    mom_pct = ((mtd - prev_same) / prev_same * 100) if prev_same > 0 else None

    # 前月末の総在庫（前月比）
    prev_end = _month_end(prev_ym)
    prev_total = sum(v for k, v in compute_stock(entries, all_out, cfg, prev_end).items()
                     if keep({"form": k[0], "variety": k[1], "location": k[2]}))
    total_pct = ((total - prev_total) / prev_total * 100) if prev_total > 0 else None

    # 在庫の推移（各月末時点の帳簿上の在庫）。基準（棚卸・入庫）が始まった月から。
    first_base = min((ledger.parse_date(e.get("entry_date")) for e in entries
                      if e.get("kind") in ("stock", "buy", "produce", "adjust", "mill")
                      and ledger.parse_date(e.get("entry_date"))), default=None)
    stock_line: list[tuple[str, float, bool]] = []  # (月, kg, 予測か)
    fut_total = total
    for ym in months:
        if first_base is None:
            break
        if ym < _month_key(first_base):
            continue
        if ym < cur_ym:
            v = sum(v for k, v in compute_stock(entries, all_out, cfg, _month_end(ym)).items()
                    if keep({"form": k[0], "variety": k[1], "location": k[2]}))
            stock_line.append((ym, round(v, 1), False))
        elif ym == cur_ym:
            stock_line.append((ym, round(total, 1), False))
        else:
            fut_total = max(fut_total - monthly_avg, 0.0)
            stock_line.append((ym, round(fut_total, 1), True))

    # --- 取引先別 月次 ---
    cust_month: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    cust_name: dict[str, str] = {}
    for r in live_out:
        cust_month[r["customer_key"]][_month_key(r["date"])] += r["kg"]
        cust_name.setdefault(r["customer_key"], r["customer"] or "（名前なし）")
    fy_tot = {ck: sum(v for m, v in mm.items() if m in months) for ck, mm in cust_month.items()}
    ranked = [ck for ck, _ in sorted(fy_tot.items(), key=lambda kv: -kv[1]) if fy_tot[ck] > 0]
    # 領収書・請求書を出す卸先（キーが b: で始まる）はすべて個別に表示する。
    # 「その他」にまとめるのは一般のお客様（消費者向け）だけ。
    whole = [ck for ck in ranked if ck.startswith("b:")]
    cons = [ck for ck in ranked if not ck.startswith("b:")]
    top = sorted(whole + cons[:5], key=lambda ck: -fy_tot[ck])

    cust_forecast: dict[str, float] = {}
    for ck in top:
        act = [cust_month[ck].get(m, 0.0) for m in span]
        if sum(1 for a in act if a > 0) >= 2:
            cust_forecast[ck] = sum(act) / 3.0
    future_months = [m for m in months if m > cur_ym]
    table = []
    for ck in top:
        row = {"取引先": cust_name[ck]}
        for m in months:
            if m <= cur_ym:
                row[m] = round(cust_month[ck].get(m, 0.0), 1)
            else:
                row[m] = round(cust_forecast[ck], 1) if ck in cust_forecast else None
        row["合計"] = round(sum(v for k, v in row.items() if k in months and v), 1)
        table.append(row)
    rest = cons[5:]
    if rest:
        row = {"取引先": f"その他の一般のお客様（{len(rest)}名）"}
        for m in months:
            if m <= cur_ym:
                row[m] = round(sum(cust_month[ck].get(m, 0.0) for ck in rest), 1)
            else:
                rest_avg = sum(cust_month[ck].get(x, 0.0) for ck in rest for x in span) / 3.0
                row[m] = round(rest_avg, 1) if rest_avg > 0 else None
        row["合計"] = round(sum(v for k, v in row.items() if k in months and v), 1)
        table.append(row)
    total_row = {"取引先": "合計"}
    for m in months:
        if m <= cur_ym:
            total_row[m] = round(out_month.get(m, 0.0), 1)
        else:
            total_row[m] = round(monthly_avg, 1) if monthly_avg else None
    total_row["合計"] = round(sum(v for k, v in total_row.items() if k in months and v), 1)
    table.append(total_row)

    # 出庫先の内訳（保管場所→取引先）
    flow: dict[tuple, float] = defaultdict(float)
    for r in live_out:
        if _month_key(r["date"]) in months:
            flow[(r["location"], r["customer_key"])] += r["kg"]

    # 高回転（直近3か月 vs その前3か月）
    hot = []
    for ck in ranked:
        a = sum(cust_month[ck].get(m, 0.0) for m in span)
        b = sum(cust_month[ck].get(m, 0.0) for m in prev_span)
        if a / 3 >= 20 and b > 0 and a >= b * 1.3:
            hot.append((cust_name[ck], a / 3, b / 3))

    usage = _client_usage(cfg=cfg, now=now, rows=live_out, clients=clients, cur_ym=cur_ym, span=span)
    plan = _plan(cfg=cfg, now=now, stock_rows=stock_rows, res_rows=res_rows, live_out=live_out,
                 out_month=out_month, cur_ym=cur_ym, span=span)
    waiting = _receipt_waiting(all_out, receipts, clients, pendings, cfg)
    _merge_usage_into_plan(plan, usage, live_out, cfg, cur_ym, span)

    alerts = _alerts(cfg=cfg, now=now, total=total, has_base=has_base, has_counts=has_counts,
                     last_count=last_count, risk=risk, run_out=run_out, months_left=months_left,
                     mtd=mtd, mom_pct=mom_pct, by_loc=by_loc, stock_rows=stock_rows,
                     reserved_by_form=reserved_by_form, by_form=by_form, hot=hot,
                     dup_n=sum(1 for r in out_rows if r["dup"] and r["source"] == "注文" and not r.get("skip")),
                     monthly_avg=monthly_avg, waiting=waiting, plan=plan)

    return {
        "fy": fy, "label": label, "now": now, "cfg": cfg, "months": months, "cur_ym": cur_ym,
        "stock_rows": stock_rows, "total": total, "by_form": by_form, "by_loc": by_loc,
        "by_var": dict(by_var), "has_counts": has_counts, "has_base": has_base,
        "last_count": last_count, "reserved_kg": reserved_kg,
        "reserved_by_form": reserved_by_form, "reserved_n": len({r["id"].split(":")[0] for r in res_rows}),
        "available": available, "out_month": dict(out_month),
        "out_month_form": {k: dict(v) for k, v in out_month_form.items()},
        "monthly_avg": monthly_avg, "months_left": months_left, "run_out": run_out,
        "risk": risk, "mtd": mtd, "prev_same": prev_same, "mom_pct": mom_pct,
        "total_pct": total_pct, "stock_line": stock_line, "table": table,
        "future_months": future_months, "flow": dict(flow), "cust_name": cust_name,
        "others": [{"取引先": cust_name[ck], "合計(kg)": round(fy_tot[ck], 1)} for ck in rest],
        "alerts": alerts, "out_rows": out_rows, "all_out": all_out,
        "forecast_text": _forecast_text(risk, run_out, months_left, cfg, now),
        "plan": plan, "receipt_waiting": waiting, "client_usage": usage,
    }


def to_white(form: str, kg: float, cfg: dict) -> float:
    """白米（精米）換算のkg。玄米は歩留まりを掛ける。"""
    y = float(cfg.get("milling_yield") or 0.9)
    return kg if form == "精米" else kg * y


def _months_between(cur_ym: str, end_ym: str) -> int:
    (y0, m0), (y1, m1) = (int(cur_ym[:4]), int(cur_ym[5:])), (int(end_ym[:4]), int(end_ym[5:]))
    return max((y1 - y0) * 12 + (m1 - m0) + 1, 1)


def _next_ym(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[5:]) - 1 + k
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def _plan(*, cfg, now, stock_rows, res_rows, live_out, out_month, cur_ym, span) -> dict:
    """月の使える量（白米換算）と、売れ残り・不足の見込み。

    ・使える量の元は「月初の在庫」（今の在庫＋今月ここまでの出庫）。これを次の新米の月までの
      月数で割ったものが「1か月あたりの使える量」。月の途中で数字が動かない。
    ・現在のペース＝直近3か月の月平均（白米換算）。今月の出庫がそれより多ければ今月はそちらを使う。
    """
    y = float(cfg.get("milling_yield") or 0.9)
    hm = int(cfg.get("harvest_month") or 9)
    end_y = now.year if now.month <= hm else now.year + 1
    end_ym = f"{end_y}-{hm:02d}"
    n = _months_between(cur_ym, end_ym)

    total_w = sum(to_white(r["form"], r["kg"], cfg) for r in stock_rows)
    genmai = sum(r["kg"] for r in stock_rows if r["form"] == "玄米")
    seimai = sum(r["kg"] for r in stock_rows if r["form"] == "精米")
    reserved_w = sum(to_white(r["form"], r["kg"], cfg) for r in res_rows)
    mtd_w = sum(to_white(r["form"], r["kg"], cfg) for r in live_out if _month_key(r["date"]) == cur_ym)
    start_w = total_w + mtd_w

    by_m: dict[str, float] = defaultdict(float)
    for r in live_out:
        by_m[_month_key(r["date"])] += to_white(r["form"], r["kg"], cfg)
    past = [by_m.get(m, 0.0) for m in span]
    has_hist = any(m < cur_ym and v > 0 for m, v in by_m.items())
    avg_w = (sum(past) / 3.0) if has_hist else mtd_w
    pace_w = max(avg_w, mtd_w)

    cap_w = max(start_w, 0.0) / n                     # 1か月あたりの使える量
    cap_left = cap_w - mtd_w                          # 今月あと使える量
    surplus_w = start_w - pace_w * n                  # 新米までに余る(+)／足りない(−)見込み
    ok = start_w > 0
    need_gap = cap_w - pace_w                         # 売り切るのに、いまのペースより増やす量

    line = []
    for k in range(n):
        ym = _next_ym(cur_ym, k)
        line.append({"ym": ym, "pace": max(start_w - pace_w * (k + 1), 0.0) if ok else 0.0,
                     "target": max(start_w - cap_w * (k + 1), 0.0) if ok else 0.0})
    return {
        "yield": y, "harvest_ym": end_ym, "n_months": n, "total_w": total_w,
        "genmai": genmai, "seimai": seimai, "genmai_w": genmai * y,
        "reserved_w": reserved_w, "mtd_w": mtd_w, "start_w": start_w,
        "avg_w": avg_w, "pace_w": pace_w, "target_w": cap_w, "cap_left": cap_left,
        "surplus_w": surplus_w if ok else None, "need_gap": need_gap, "line": line,
        "has_hist": has_hist,
    }


def _client_usage(*, cfg, now, rows, clients, cur_ym, span) -> dict:
    """卸先ごとの「月の使用量（計画）」に対する今月の使用状況。

    実績は、領収書・確定済みの請求書として出庫に数えているもの（白米換算）。
    今月の見込みは、ここまでの実績に「直近3か月の平均 × 月の残り割合」を足したもの
    （平均が無ければ、ここまでのペースを月末まで延ばす。月の前半は延ばさない）。
    """
    import calendar
    plans = {k: _f(v) for k, v in (cfg.get("client_monthly_kg") or {}).items()}
    dim = calendar.monthrange(now.year, now.month)[1]
    frac = now.day / dim
    by_c: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        cl = r.get("b2b_client")
        if cl and r["source"] != "注文":
            by_c[cl][_month_key(r["date"])] += to_white(r["form"], r["kg"], cfg)
    out = []
    for c in clients:
        cid = c["id"]
        plan = plans.get(cid, 0.0)
        used = by_c[cid].get(cur_ym, 0.0) if cid in by_c else 0.0
        avg3 = (sum(by_c[cid].get(m, 0.0) for m in span) / 3.0) if cid in by_c else 0.0
        if plan <= 0 and used <= 0 and avg3 <= 0:
            continue
        if avg3 > 0:
            proj = used + avg3 * (1 - frac)
        elif now.day >= 10 and frac > 0:
            proj = used / frac
        else:
            proj = used
        proj = max(proj, used)
        out.append({"id": cid, "name": _clean_name(c.get("name") or "").rstrip("様").strip(),
                    "plan": plan, "used": used, "avg3": avg3, "proj": proj,
                    "remaining": plan - used, "surplus": (plan - proj) if plan > 0 else None})
    out.sort(key=lambda d: (-d["plan"], -d["used"]))
    planned = [d for d in out if d["plan"] > 0]
    tot = {
        "plan": sum(d["plan"] for d in planned), "used": sum(d["used"] for d in planned),
        "proj": sum(d["proj"] for d in planned),
        "unplanned_used": sum(d["used"] for d in out if d["plan"] <= 0),
        "frac": frac,
    }
    tot["surplus"] = tot["plan"] - tot["proj"]
    return {"rows": out, "total": tot, "frac": frac}


def _merge_usage_into_plan(plan, usage, live_out, cfg, cur_ym, span) -> None:
    """卸の計画合計と消費者向けの月平均を、全体の使える量（白米換算）と突き合わせる。"""
    cons: dict[str, float] = defaultdict(float)
    for r in live_out:
        if r["source"] == "注文" and not r.get("b2b_client"):
            cons[_month_key(r["date"])] += to_white(r["form"], r["kg"], cfg)
    has_hist = any(m < cur_ym and v > 0 for m, v in cons.items())
    cons_avg = (sum(cons.get(m, 0.0) for m in span) / 3.0) if has_hist else cons.get(cur_ym, 0.0)
    tot = usage["total"]
    expected = tot["plan"] + cons_avg
    plan["wholesale_plan"] = tot["plan"]
    plan["consumer_avg"] = cons_avg
    plan["consumer_mtd"] = cons.get(cur_ym, 0.0)
    plan["month_expected"] = expected
    plan["month_room"] = plan["target_w"] - expected     # 計画どおり売れたあと、1か月の枠に残る(+)／超える(−)量


def _receipt_waiting(all_out, receipts, clients, pendings, cfg=None) -> list[dict]:
    """請求先ごとに、最後の領収書（確定済み請求書で数える設定なら請求書も）より後に出荷した注文
    （＝領収書を出すまで在庫から引かれない量）。"""
    last: dict[str, date] = {}
    docs = [(v.get("client_id"), v.get("issue_date")) for v in (receipts or {}).values()]
    if (cfg or {}).get("count_sent_invoices", True):
        docs += [(p.get("client_id"), p.get("issue_date")) for p in (pendings or {}).values()
                 if p.get("status") == "sent"]
    for cl, iso in docs:
        d = ledger.parse_date(iso)
        if d and (cl not in last or d > last[cl]):
            last[cl] = d
    names = {c["id"]: _clean_name(c.get("name") or "").rstrip("様").strip() for c in clients}
    agg: dict[str, float] = defaultdict(float)
    for r in all_out:
        cl = r.get("b2b_client")
        if r["source"] != "注文" or not cl or r["dup"] or r["kg"] <= 0:
            continue
        if cl not in last or r["date"] > last[cl]:
            agg[cl] += r["kg"]
    return [{"client": names.get(cl, cl), "kg": kg}
            for cl, kg in sorted(agg.items(), key=lambda t: -t[1]) if kg >= 1]


def _forecast_text(risk, run_out, months_left, cfg, now):
    if risk is None:
        return None
    if risk in ("高", "中") and run_out is not None:
        return (f'現在の出庫ペースが続いた場合、{_period_text(run_out)}ごろに在庫がなくなる見込みです。')
    if months_left is not None:
        return f"現在の出庫ペースなら、あと約{months_left:.1f}か月ぶんの在庫があります。"
    return None


def _alerts(*, cfg, now, total, has_base, has_counts, last_count, risk, run_out, months_left,
            mtd, mom_pct, by_loc, stock_rows, reserved_by_form, by_form, hot, dup_n,
            monthly_avg, waiting=None, plan=None) -> list[dict]:
    out = []

    def add(level, tag, text):
        out.append({"level": level, "tag": tag, "text": text})

    if not has_counts:
        add("warn", "棚卸未登録", "現在の在庫がまだ登録されていません。「在庫の登録・修正」で"
            "保管場所ごとの実数（棚卸）を入れると、正しい在庫が出ます。")
    elif last_count and (now - last_count).days > cfg["stale_count_days"]:
        add("warn", "棚卸が古い", f"最後の棚卸は{last_count:%Y/%m/%d}です（{(now - last_count).days}日前）。"
            "実数を数え直して登録すると、ずれが直ります。")
    if risk == "高":
        add("danger", "欠品リスク", "在庫が逼迫しています。" + (
            f"{_period_text(run_out)}ごろに底をつく見込みです。" if run_out else "追加の仕入・とう精を検討してください。"))
    elif risk == "中":
        add("warn", "在庫注意", f"あと約{months_left:.1f}か月ぶんの在庫です（{_period_text(run_out)}ごろ）。")
    short = reserved_by_form.get("精米", 0) - by_form.get("精米", 0)
    if has_base and reserved_by_form.get("精米", 0) > 0 and short > 0:
        gen = by_form.get("玄米", 0)
        add("warn", "精米不足", f"未出荷の注文に対して精米が約{short:,.0f}kg足りません。"
            + (f"玄米が{gen:,.0f}kgあるので、とう精を進めてください。" if gen > 0 else ""))
    if mom_pct is not None and mom_pct >= 20 and mtd >= 30:
        add("info", "出庫増加", f"今月の出庫は前月の同じ時期より{mom_pct:+.0f}%多くなっています。")
    for name, d in by_loc.items():
        cap = next((l.get("capacity_kg") or 0 for l in cfg["locations"] if l["name"] == name), 0)
        if cap and d["total"] / cap >= 0.9:
            add("warn", "保管場所が満杯", f"{name}が容量の{d['total'] / cap * 100:.0f}%です。")
    if len([1 for d in by_loc.values() if d["total"] > 0]) >= 2 and total > 0:
        big = max(by_loc.items(), key=lambda kv: kv[1]["total"])
        if big[1]["total"] / total >= 0.8:
            add("info", "在庫の偏り", f"{big[0]}に在庫の{big[1]['total'] / total * 100:.0f}%が集中しています。")
    unset = by_loc.get(UNSET_LOC, {}).get("total", 0)
    if abs(unset) > 0:
        add("info", "場所未指定", f"保管場所が未指定の在庫が{unset:,.0f}kgあります。")
    neg = [r for r in stock_rows if r["kg"] < -0.05]
    if neg and has_base:
        names = "、".join(f'{r["variety"]}{r["form"]}（{r["location"]}）' for r in neg[:3])
        add("warn", "マイナス在庫", f"{names}がマイナスです。入庫や棚卸の記録を確認してください。")
    for name, a, b in hot[:2]:
        add("info", "高回転取引先", f"{name}の直近3か月の月平均は{a:,.0f}kgで、その前の3か月（{b:,.0f}kg）より増えています。")
    for w in (waiting or [])[:4]:
        add("info", "領収書待ち", f"{w['client']}：領収書（請求書）の発行前に出荷した分が約{w['kg']:,.0f}kgあります"
            "（発行すると在庫から引かれます）。")
    if plan and plan.get("surplus_w") is not None and plan["surplus_w"] > 0.15 * max(plan["start_w"], 1):
        add("warn", "在庫が余る見込み",
            f"このペースだと次の新米までに白米換算で約{plan['surplus_w']:,.0f}kg余ります。"
            f"月{plan['target_w']:,.0f}kgを目安に売りたいところです。")
    if dup_n:
        add("info", "重複を除外", f"同じ伝票番号の注文{dup_n}件を、二重に数えないよう自動で除外しています。")
    return out


# ---------------------------------------------------------------------------
# 書き込み（帳簿と共通の ledger_entries へ）
# ---------------------------------------------------------------------------
def save_counts(rows: list[dict], on: date) -> int:
    """棚卸（実数）を登録する。rows: {form, variety, location, kg}。"""
    flags = ledger.load_flags()
    n = 0
    for r in rows:
        if r.get("kg") is None:
            continue
        db.add_ledger_entry({
            "entry_date": on.isoformat(), "kind": "stock",
            "rice_type": ledger.product_rice_type(r["variety"], flags),
            "form": r["form"], "qty_kg": float(r["kg"]), "qty_out_kg": 0,
            "counterparty": "", "note": "在庫画面の棚卸",
            "location": "" if r["location"] == UNSET_LOC else r["location"],
            "variety": r["variety"],
        })
        n += 1
    return n
