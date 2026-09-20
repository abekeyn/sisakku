# -*- coding: utf-8 -*-
"""在庫管理ダッシュボードの集計（画面とは切り離した純粋なロジック）。

元データ
- 出庫 … 注文（発送済み）＋ 手入力の請求書。ただし
    ・同じ伝票番号の注文は1件として数える（LINE入力の注文とヤマト履歴の取込行が
      同じ出荷を二重に持っていることがあるため）
    ・請求書のうち、同じ取引先の近い日付の注文に既に入っている量は差し引く
      （注文と請求書は同じ出荷を別々に記録しているため二重に数えない）
    ・自動作成の請求書（ヤマト集計）は注文の集計そのものなので数えない
- 入庫・棚卸・手修正 … 食糧法の帳簿と共通の ledger_entries
    （棚卸＝その場所・品種・形態の実数。以後の入出庫を足し引きして現在庫を出す）

在庫は「形態（玄米/精米）× 品種 × 保管場所」ごとに持つ。
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
    "include_pending_invoices": True,     # 未確定（下書き）の手入力請求書も出庫に含める
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


def build_movements(orders, pendings, clients, cfg, flags):
    """出庫行（在庫から出たもの）と確保行（未出荷の注文）を作る。

    returns (out_rows, reserved_rows)
    out_rows の各要素: id / date / customer_key / customer / source / variety /
                       form / kg / location / dup / excluded / note
    """
    excluded = set(cfg.get("excluded_out") or [])
    out: list[dict] = []
    reserved: list[dict] = []

    shipped = [o for o in orders if o.get("status") == "shipped"]
    shipped.sort(key=lambda o: (_order_date(o) or date.min, int(o.get("id") or 0)))
    seen_tracking: set[tuple] = set()
    order_rows: list[dict] = []
    for o in shipped:
        d = _order_date(o)
        if d is None:
            continue
        parts, _ = ledger.split_order(o, flags)
        if not parts:
            continue
        trk = (o.get("tracking_no") or "").strip()
        dup = False
        if trk:
            k = (int(o.get("customer_id") or 0), trk)
            dup = k in seen_tracking
            seen_tracking.add(k)
        variety = detect_variety(o.get("product_name") or "", cfg)
        cname = _clean_name(o.get("customer_name") or "")
        ckey = f'c{int(o.get("customer_id") or 0)}'
        for (rt, fm), kg in parts.items():
            row = {
                "id": f'o{int(o["id"])}:{fm}', "date": d, "customer_key": ckey,
                "customer": cname, "customer_id": int(o.get("customer_id") or 0),
                "source": "注文", "variety": variety, "form": fm, "kg": float(kg),
                "location": _default_loc(fm, cfg, variety), "dup": dup,
                "excluded": f'o{int(o["id"])}:{fm}' in excluded,
                "note": "同じ伝票番号の注文と重複" if dup else "",
                "product": o.get("product_name") or "",
            }
            order_rows.append(row)
            out.append(row)

    for o in orders:
        if o.get("status") == "shipped":
            continue
        parts, _ = ledger.split_order(o, flags)
        variety = detect_variety(o.get("product_name") or "", cfg)
        for (rt, fm), kg in parts.items():
            reserved.append({"id": f'o{int(o["id"])}:{fm}', "form": fm, "kg": float(kg),
                             "variety": variety, "location": _default_loc(fm, cfg, variety),
                             "customer": _clean_name(o.get("customer_name") or "")})

    # 手入力の請求書（注文に無い出庫）
    cl_by_id = {c["id"]: c for c in clients}
    live = [r for r in order_rows if not r["dup"] and not r["excluded"]]
    consumed: set[str] = set()
    invs = []
    for k, p in pendings.items():
        if p.get("source") != "manual":
            continue
        if p.get("status") != "sent" and not cfg.get("include_pending_invoices", True):
            continue
        d = ledger.parse_date(p.get("issue_date"))
        if d is None:
            continue
        invs.append((d, k, p))
    invs.sort(key=lambda t: (t[0], t[1]))
    for d, k, p in invs:
        c = cl_by_id.get(p.get("client_id"), {})
        cid = int(c.get("customer_id") or 0)
        item = c.get("item_desc") or ""
        form = "玄米" if ("玄米" in item and "精米" not in item) else "精米"
        kg = _f(p.get("total_kg"))
        need = kg
        # 同じ取引先・±3日の注文に既に入っている分を差し引く（近い日付から順に消費）
        near = sorted((r for r in live if r["customer_id"] == cid and cid
                       and r["id"] not in consumed and abs((r["date"] - d).days) <= 3),
                      key=lambda r: (abs((r["date"] - d).days), r["date"]))
        covered = 0.0
        for r in near:
            if need <= 0:
                break
            consumed.add(r["id"])
            need -= r["kg"]
            covered += r["kg"]
        rem = max(need, 0.0)
        cname = _clean_name(c.get("name") or p.get("client_name") or "").rstrip("様").strip()
        note = ("手入力の請求書" if not covered else
                f"手入力の請求書（注文に含まれる {min(covered, kg):g}kg を除く）")
        if p.get("status") != "sent":
            note += "・未確定"
        out.append({
            "id": f"i{k}", "date": d, "customer_key": f"c{cid}" if cid else f"n{cname}",
            "customer": cname, "customer_id": cid, "source": "請求書",
            "variety": detect_variety(item, cfg), "form": form, "kg": rem,
            "location": _default_loc(form, cfg, detect_variety(item, cfg)), "dup": False,
            "excluded": f"i{k}" in excluded, "note": note, "product": item,
            "invoice_kg": kg,
        })
    out.sort(key=lambda r: (r["date"], r["id"]))
    return out, reserved


def counted(rows):
    """在庫計算に使う出庫行（重複・手動除外・0kgを除く）。"""
    return [r for r in rows if not r["dup"] and not r["excluded"] and r["kg"] > 0]


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

    その組み合わせの直近の棚卸を基準に、それ以後の入庫・出庫を足し引きする。
    棚卸が無い組み合わせは、記録された入庫から出庫を引いた値になる（0起点）。
    """
    base: dict[tuple, tuple[date, float]] = {}
    for e in entries:
        if e.get("kind") != "stock":
            continue
        d = ledger.parse_date(e.get("entry_date"))
        if d is None or d > as_of:
            continue
        k = _entry_key(e, cfg)
        if k not in base or d >= base[k][0]:
            base[k] = (d, _f(e.get("qty_kg")))

    events: list[tuple[date, tuple, float]] = []
    for e in entries:
        d = ledger.parse_date(e.get("entry_date"))
        kind = e.get("kind")
        if d is None or d > as_of or kind == "stock":
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
        if r["date"] <= as_of:
            events.append((r["date"], (r["form"], r["variety"], r["location"]), -r["kg"]))

    stock: dict[tuple, float] = defaultdict(float)
    for k, (_d, q) in base.items():
        stock[k] = q
    for d, k, delta in events:
        if k in base and d <= base[k][0]:
            continue  # 棚卸日までの動きは実数に織り込み済み
        stock[k] += delta
    return {k: round(v, 2) for k, v in stock.items()
            if abs(v) > 1e-9 or k in base}


def _month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def range_months(start: date, end: date, limit: int = 36) -> list[str]:
    """start〜end にかかる月（'YYYY-MM'）を古い順に。最大 limit か月。"""
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month) and len(out) < limit:
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fy_months(fy: int) -> list[str]:
    return [f"{fy}-{m:02d}" if m >= 4 else f"{fy + 1}-{m:02d}" for m in (4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3)]


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
          orders=None, entries=None, pendings=None, clients=None) -> dict:
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

    all_out, reserved = build_movements(orders, pendings, clients, cfg, flags)

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
    # 対象期間：年度（4月〜翌3月）または、開始日・終了日の自由な期間（月単位で表示）
    if start and end:
        months = range_months(start, end)
        label = f"{start:%Y/%m/%d}〜{end:%Y/%m/%d}"
    else:
        fy = fy if fy is not None else ledger.fy_of(now)
        months = fy_months(fy)
        label = f"{fy}年度"
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
    top = ranked[:8]

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
    rest = [ck for ck in ranked[8:]]
    if rest:
        row = {"取引先": f"その他（{len(rest)}名）"}
        for m in months:
            if m <= cur_ym:
                row[m] = round(sum(cust_month[ck].get(m, 0.0) for ck in rest), 1)
            else:
                row[m] = round(max(monthly_avg - sum(cust_forecast.values()), 0.0), 1) if monthly_avg else None
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

    alerts = _alerts(cfg=cfg, now=now, total=total, has_base=has_base, has_counts=has_counts,
                     last_count=last_count, risk=risk, run_out=run_out, months_left=months_left,
                     mtd=mtd, mom_pct=mom_pct, by_loc=by_loc, stock_rows=stock_rows,
                     reserved_by_form=reserved_by_form, by_form=by_form, hot=hot,
                     dup_n=sum(1 for r in out_rows if r["dup"] and r["source"] == "注文"),
                     monthly_avg=monthly_avg)

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
    }


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
            monthly_avg) -> list[dict]:
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
