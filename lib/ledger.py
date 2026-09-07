# -*- coding: utf-8 -*-
"""食糧法第48条の帳簿（米穀の種類別 買受・販売・在庫）。

届出事業者は帳簿を備え、必要事項を記載して**3年間保存**する義務がある
（不備・虚偽・未保存は20万円以下の過料）。農水省Q&A A12により「種類」とは
**うるち／もち**と**玄米／精米**の区分を指し、必要最小限の記載事項は種類別の
①買受数量 ②販売数量 ③在庫数量。

このモジュールは
- 既存の注文データ（orders×products）から**販売数量**を自動で積み上げ、
- アプリでは分からない**買受（仕入）・自家生産入庫・とう精・実地棚卸**は
  `ledger_entries` の手入力で補い（注文データは一切書き換えない＝差分で持つ）、
- 年度（4/1〜翌3/31）単位でCSV／PDFに出力する。

あわせて法第47条の届出要否に使う「玄米×0.91で精米換算した年間の出荷・販売
数量」を計算する。無償譲渡（Q&A A4）と、自家生産米を届出事業者へ出荷・販売
した分（Q&A A8）は20精米トン判定から除外できるので、その区分も持つ。
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta, timezone

from . import db

JST = timezone(timedelta(hours=9))

# 玄米→精米の換算係数（農水省Q&A A3：「玄米量×0.91」）
GENMAI_TO_SEIMAI = 0.91

# 届出義務が生じる事業規模（法第47条第1項）
THRESHOLD_TON = 20.0

RICE_TYPES = ("うるち", "もち")
FORMS = ("玄米", "精米")
# 帳簿上の「種類」＝ うるち/もち × 玄米/精米 の4区分
KINDS_OF_RICE = [(rt, fm) for rt in RICE_TYPES for fm in FORMS]

# 手入力する記録の種別
ENTRY_KINDS = {
    "buy": "買受（仕入）",
    "produce": "自家生産入庫",
    "mill": "とう精（玄米→精米）",
    "stock": "在庫（実地棚卸）",
    "adjust": "その他増減",
}

EXCL_FREE = "無償譲渡"
EXCL_TODOKEDE = "届出事業者へ出荷（自家生産米）"

# PDFの明細欄に収まる短縮表記（CSVは正式名称のまま）
_EXCL_SHORT = {EXCL_FREE: "無償譲渡", EXCL_TODOKEDE: "届出事業者向け"}


def today() -> date:
    return datetime.now(JST).date()


# ---------------------------------------------------------------------------
# 年度（4/1〜翌3/31）
# ---------------------------------------------------------------------------
def fy_of(d: date) -> int:
    """その日が属する年度（4月始まり）。2027-03-31 → 2026。"""
    return d.year if d.month >= 4 else d.year - 1


def fy_range(fy: int) -> tuple[date, date]:
    return date(fy, 4, 1), date(fy + 1, 3, 31)


def fy_label(fy: int) -> str:
    """「2026年度（令和8年度）」。令和1年＝2019年。"""
    return f"{fy}年度（令和{fy - 2018}年度）"


def parse_date(s) -> date | None:
    """'2026-04-01' '2026/4/1' などを date に。取れなければ None。"""
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", str(s or ""))
    if not m:
        return None
    try:
        return date(*(int(x) for x in m.groups()))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 区分の設定（settings に差分で保存。注文・顧客・商品の実データは書き換えない）
# ---------------------------------------------------------------------------
FLAGS_KEY = "ledger_flags"

_DEFAULT_FLAGS = {
    # 商品名 → "うるち" / "もち"（未設定は商品名から自動判定）
    "rice_type_by_product": {},
    # 届出事業者（第47条の届出をしている取引先）の顧客ID
    "todokede_customers": [],
    # 無償譲渡（贈答・サンプル）の注文ID
    "free_orders": [],
    # 販売する米はすべて自家生産米か（Q&A A8の除外はこれが前提）
    "own_production": True,
    # 事業者名（帳簿の表題に印字）
    "business_name": "阿部農園",
}

_MOCHI_RE = re.compile(r"もち|モチ|餅|糯")


def load_flags() -> dict:
    saved = db.get_setting(FLAGS_KEY) or {}
    flags = dict(_DEFAULT_FLAGS)
    flags["rice_type_by_product"] = dict(_DEFAULT_FLAGS["rice_type_by_product"])
    flags.update({k: v for k, v in saved.items() if k in _DEFAULT_FLAGS})
    return flags


def save_flags(flags: dict) -> None:
    db.set_setting(FLAGS_KEY, {k: flags.get(k, _DEFAULT_FLAGS[k]) for k in _DEFAULT_FLAGS})


def product_rice_type(product_name: str, flags: dict) -> str:
    """商品の「うるち／もち」区分。設定が無ければ商品名から推定（既定はうるち）。"""
    override = (flags.get("rice_type_by_product") or {}).get(product_name or "")
    if override in RICE_TYPES:
        return override
    return "もち" if _MOCHI_RE.search(product_name or "") else "うるち"


# ---------------------------------------------------------------------------
# 注文 → 種類別の数量
# ---------------------------------------------------------------------------
def split_order(o: dict, flags: dict) -> tuple[dict, bool]:
    """1注文を種類別kgに割る。

    returns ({(うるち/もち, 玄米/精米): kg}, 要確認フラグ)

    - 精米商品（needs_milling=1）はそのまま精米
    - 玄米商品はそのまま玄米
    - 複合商品は milling_kg_override（1個あたりの精米kg）で精米／玄米に分ける。
      未入力なら全量を玄米として扱い、要確認フラグを立てる
    - やさい等（重量0・その他）は0
    """
    qty = int(o.get("qty") or 1)
    weight = float(o.get("weight_kg") or 0)
    cat = o.get("category") or ""
    rt = product_rice_type(o.get("product_name") or "", flags)
    parts: dict[tuple[str, str], float] = {}
    needs_check = False

    if weight <= 0:
        return parts, needs_check

    if cat == "複合":
        mill = float(o.get("milling_kg_override") or 0)
        if mill <= 0:
            parts[(rt, "玄米")] = weight * qty
            needs_check = True
        else:
            mill = min(mill, weight)
            parts[(rt, "精米")] = mill * qty
            if weight - mill > 0:
                parts[(rt, "玄米")] = (weight - mill) * qty
    elif cat == "精米" or o.get("needs_milling"):
        parts[(rt, "精米")] = weight * qty
    elif cat == "玄米":
        parts[(rt, "玄米")] = weight * qty
    return parts, needs_check


def exclusion_reason(o: dict, flags: dict) -> str:
    """20精米トン判定から外せる注文かどうか。外せなければ空文字。"""
    if int(o.get("id") or 0) in set(flags.get("free_orders") or []):
        return EXCL_FREE
    if flags.get("own_production") and \
            int(o.get("customer_id") or 0) in set(flags.get("todokede_customers") or []):
        return EXCL_TODOKEDE
    return ""


def seimai_equiv(genmai_kg: float, seimai_kg: float) -> float:
    """精米換算kg（玄米×0.91＋精米）。"""
    return genmai_kg * GENMAI_TO_SEIMAI + seimai_kg


def sales_detail(orders, start: date, end: date, flags: dict) -> list[dict]:
    """期間内の販売明細（1注文1行）。日付の取れない注文は除く。"""
    rows = []
    for o in orders:
        d = parse_date(o.get("order_date")) or parse_date(o.get("ship_date")) \
            or parse_date(o.get("created_at"))
        if d is None or not (start <= d <= end):
            continue
        parts, needs_check = split_order(o, flags)
        if not parts:
            continue
        rt = next(iter(parts))[0]
        genmai = sum(v for (r, f), v in parts.items() if f == "玄米")
        seimai = sum(v for (r, f), v in parts.items() if f == "精米")
        excl = exclusion_reason(o, flags)
        rows.append({
            "注文ID": int(o.get("id") or 0),
            "顧客ID": int(o.get("customer_id") or 0),
            "日付": d,
            "顧客": o.get("customer_name") or "",
            "商品": o.get("product_name") or "",
            "個数": int(o.get("qty") or 1),
            "うるち/もち": rt,
            "玄米kg": round(float(genmai), 2),
            "精米kg": round(float(seimai), 2),
            "精米換算kg": round(seimai_equiv(genmai, seimai), 2),
            "20トン判定": "除外" if excl else "対象",
            "除外理由": excl,
            "要確認": needs_check,
        })
    rows.sort(key=lambda r: (r["日付"], r["注文ID"]))
    return rows


# ---------------------------------------------------------------------------
# 手入力の記録 → 種類別の増減
# ---------------------------------------------------------------------------
def _entry_key(e: dict) -> tuple[str, str]:
    rt = e.get("rice_type") if e.get("rice_type") in RICE_TYPES else "うるち"
    fm = e.get("form") if e.get("form") in FORMS else "玄米"
    return rt, fm


def opening_stock(entries, start: date) -> dict[tuple[str, str], float | None]:
    """期首在庫＝期間開始日より前の、直近の実地棚卸。無ければ None。"""
    latest: dict[tuple[str, str], tuple[date, float]] = {}
    for e in entries:
        if e.get("kind") != "stock":
            continue
        d = parse_date(e.get("entry_date"))
        if d is None or d >= start:
            continue
        k = _entry_key(e)
        if k not in latest or d >= latest[k][0]:
            latest[k] = (d, float(e.get("qty_kg") or 0))
    return {k: (latest[k][1] if k in latest else None) for k in KINDS_OF_RICE}


def closing_stock_counted(entries, start: date, end: date):
    """期末在庫（実地）＝期間内の直近の実地棚卸。無ければ None。"""
    latest: dict[tuple[str, str], tuple[date, float]] = {}
    for e in entries:
        if e.get("kind") != "stock":
            continue
        d = parse_date(e.get("entry_date"))
        if d is None or not (start <= d <= end):
            continue
        k = _entry_key(e)
        if k not in latest or d >= latest[k][0]:
            latest[k] = (d, float(e.get("qty_kg") or 0))
    return ({k: (latest[k][1] if k in latest else None) for k in KINDS_OF_RICE},
            {k: (latest[k][0] if k in latest else None) for k in KINDS_OF_RICE})


# ---------------------------------------------------------------------------
# 帳簿の集計
# ---------------------------------------------------------------------------
def summarize(orders, entries, start: date, end: date, flags: dict) -> dict:
    """種類別の 買受数量・販売数量・在庫数量ほかをまとめる。"""
    zero = {k: 0.0 for k in KINDS_OF_RICE}
    buy = dict(zero)
    produce = dict(zero)
    mill = dict(zero)      # とう精による増減（玄米はマイナス・精米はプラス）
    adjust = dict(zero)
    sold = dict(zero)

    for e in entries:
        d = parse_date(e.get("entry_date"))
        if d is None or not (start <= d <= end):
            continue
        k = _entry_key(e)
        qty = float(e.get("qty_kg") or 0)
        kind = e.get("kind")
        if kind == "buy":
            buy[k] += qty
        elif kind == "produce":
            produce[k] += qty
        elif kind == "adjust":
            adjust[k] += qty
        elif kind == "mill":
            # 投入は玄米・産出は精米（rice_type は据え置き）
            mill[(k[0], "玄米")] -= qty
            mill[(k[0], "精米")] += float(e.get("qty_out_kg") or 0)

    details = sales_detail(orders, start, end, flags)
    for r in details:
        sold[(r["うるち/もち"], "玄米")] += r["玄米kg"]
        sold[(r["うるち/もち"], "精米")] += r["精米kg"]

    opening = opening_stock(entries, start)
    counted, counted_at = closing_stock_counted(entries, start, end)

    rows = []
    for k in KINDS_OF_RICE:
        op = opening[k] or 0.0
        book = op + buy[k] + produce[k] + mill[k] + adjust[k] - sold[k]
        cnt = counted[k]
        rows.append({
            "種類": f"{k[0]}・{k[1]}",
            "_key": k,
            "期首在庫kg": round(op, 1),
            "期首在庫未登録": opening[k] is None,
            "買受数量kg": round(buy[k], 1),
            "自家生産入庫kg": round(produce[k], 1),
            "とう精増減kg": round(mill[k], 1),
            "その他増減kg": round(adjust[k], 1),
            "販売数量kg": round(sold[k], 1),
            "期末在庫kg(帳簿)": round(book, 1),
            "期末在庫kg(実地)": None if cnt is None else round(cnt, 1),
            "棚卸日": counted_at[k],
            "在庫数量kg": round(book if cnt is None else cnt, 1),
            "差異kg": None if cnt is None else round(cnt - book, 1),
        })

    judge = _judge(details)
    warnings = _warnings(rows, details, flags)
    return {"rows": rows, "details": details, "judge": judge,
            "warnings": warnings, "start": start, "end": end}


def _judge(details) -> dict:
    """20精米トン判定（対象＝無償譲渡・届出事業者向けを除いた出荷・販売数量）。"""
    tot_g = tot_s = 0.0
    tgt_g = tgt_s = 0.0
    free = todokede = 0.0
    for r in details:
        tot_g += r["玄米kg"]
        tot_s += r["精米kg"]
        if r["除外理由"] == EXCL_FREE:
            free += r["精米換算kg"]
        elif r["除外理由"] == EXCL_TODOKEDE:
            todokede += r["精米換算kg"]
        else:
            tgt_g += r["玄米kg"]
            tgt_s += r["精米kg"]
    target = seimai_equiv(tgt_g, tgt_s)
    return {
        "全体_玄米kg": round(tot_g, 1),
        "全体_精米kg": round(tot_s, 1),
        "全体_精米換算kg": round(seimai_equiv(tot_g, tot_s), 1),
        "除外_無償譲渡kg": round(free, 1),
        "除外_届出事業者kg": round(todokede, 1),
        "対象_玄米kg": round(tgt_g, 1),
        "対象_精米kg": round(tgt_s, 1),
        "対象_精米換算kg": round(target, 1),
        "対象_精米トン": round(target / 1000, 3),
        "届出要否": "要届出（20精米トン以上）" if target / 1000 >= THRESHOLD_TON
                    else "20精米トン未満（届出は任意）",
        "超過": target / 1000 >= THRESHOLD_TON,
    }


def _warnings(rows, details, flags) -> list[str]:
    out = []
    n_check = sum(1 for r in details if r["要確認"])
    if n_check:
        out.append(f"複合商品で精米kgが未入力の注文が{n_check}件あります。"
                   "全量を玄米として集計しています（注文画面で精米kgを入れると正確になります）。")
    if all(r["期首在庫未登録"] for r in rows):
        out.append("期首在庫が未登録です。期間開始日より前の日付で「在庫（実地棚卸）」を"
                   "1件入れると、期末在庫の帳簿計算が正しくなります。")
    if all(r["買受数量kg"] == 0 and r["自家生産入庫kg"] == 0 for r in rows):
        out.append("買受数量・自家生産入庫が未入力です。仕入や収穫の入庫を記録してください。")
    minus = [r["種類"] for r in rows if r["在庫数量kg"] < 0]
    if minus:
        out.append(f'在庫数量がマイナスになっています（{"、".join(minus)}）。'
                   "入庫やとう精の記録が足りない可能性があります。"
                   "とう精（玄米→精米）を記録すると、玄米が減って精米が増えます。")
    if not flags.get("own_production") and flags.get("todokede_customers"):
        out.append("「販売する米はすべて自家生産米」がオフのため、届出事業者向けの除外は"
                   "適用されていません（Q&A A8の除外は自家生産米が前提です）。")
    return out


# ---------------------------------------------------------------------------
# 出力（CSV / PDF）
# ---------------------------------------------------------------------------
SUMMARY_COLS = ["種類", "期首在庫kg", "買受数量kg", "自家生産入庫kg", "とう精増減kg",
                 "その他増減kg", "販売数量kg", "期末在庫kg(帳簿)", "期末在庫kg(実地)",
                 "在庫数量kg", "差異kg"]


def fmt(v) -> str:
    """PDF・画面向けの表示（3桁区切り）。"""
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.1f}"
    if isinstance(v, date):
        return v.strftime("%Y/%m/%d")
    return str(v)


def _num(v) -> str:
    """CSV向けの数値（区切り記号なし。Excelで数値として集計できるように）。"""
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return f"{float(v):.1f}"
    return str(v)


def build_csv(fy: int, summary: dict, entries, flags: dict) -> bytes:
    """年度単位の帳簿CSV（Excelでそのまま開けるようUTF-8 BOM付き）。"""
    start, end = summary["start"], summary["end"]
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")

    w.writerow(["米穀の出荷・販売に係る帳簿（食糧法第48条）"])
    w.writerow(["事業者", flags.get("business_name", "")])
    w.writerow(["対象期間", f"{start:%Y/%m/%d}〜{end:%Y/%m/%d}", fy_label(fy)])
    w.writerow(["作成日", today().strftime("%Y/%m/%d")])
    w.writerow([])

    w.writerow(["■ 種類別 数量（kg）"])
    w.writerow(SUMMARY_COLS)
    for r in summary["rows"]:
        w.writerow([r[c] if c == "種類" else _num(r[c]) for c in SUMMARY_COLS])
    w.writerow([])

    j = summary["judge"]
    w.writerow(["■ 20精米トン判定（法第47条／玄米×0.91で精米換算）"])
    w.writerow(["出荷・販売 合計（精米換算kg）", _num(j["全体_精米換算kg"])])
    w.writerow([f"うち除外 {EXCL_FREE}（精米換算kg）", _num(j["除外_無償譲渡kg"])])
    w.writerow([f"うち除外 {EXCL_TODOKEDE}（精米換算kg）", _num(j["除外_届出事業者kg"])])
    w.writerow(["判定対象（精米換算kg）", _num(j["対象_精米換算kg"])])
    w.writerow(["判定対象（精米トン）", f'{j["対象_精米トン"]:.3f}'])
    w.writerow(["判定", j["届出要否"]])
    w.writerow([])

    w.writerow(["■ 買受・入庫・棚卸の記録（手入力）"])
    w.writerow(["日付", "区分", "うるち/もち", "玄米/精米", "数量kg",
                "とう精産出kg", "相手方", "備考"])
    for e in sorted(entries, key=lambda x: (str(x.get("entry_date") or ""), x.get("id") or 0)):
        d = parse_date(e.get("entry_date"))
        if d is None or not (start <= d <= end):
            continue
        w.writerow([f"{d:%Y/%m/%d}", ENTRY_KINDS.get(e.get("kind"), e.get("kind")),
                    e.get("rice_type") or "", e.get("form") or "",
                    _num(e.get("qty_kg") or 0),
                    _num(e.get("qty_out_kg") or 0) if e.get("kind") == "mill" else "",
                    e.get("counterparty") or "", e.get("note") or ""])
    w.writerow([])

    w.writerow(["■ 販売明細（注文データから自動集計）"])
    w.writerow(["日付", "顧客", "商品", "個数", "うるち/もち", "玄米kg", "精米kg",
                "精米換算kg", "20トン判定", "除外理由"])
    for r in summary["details"]:
        w.writerow([f'{r["日付"]:%Y/%m/%d}', r["顧客"], r["商品"], r["個数"],
                    r["うるち/もち"], _num(r["玄米kg"]), _num(r["精米kg"]),
                    _num(r["精米換算kg"]), r["20トン判定"], r["除外理由"]])

    return "﻿".encode("utf-8") + buf.getvalue().encode("utf-8")


def csv_filename(fy: int) -> str:
    return f"帳簿_{fy}年度_令和{fy - 2018}年度.csv"


def pdf_filename(fy: int) -> str:
    return f"帳簿_{fy}年度_令和{fy - 2018}年度.pdf"


def build_pdf(fy: int, summary: dict, entries, flags: dict) -> bytes:
    """年度単位の帳簿PDF（A4横・3年保存用）。"""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas as _canvas

    from . import pdf_common as pc

    pc.ensure_font()
    start, end = summary["start"], summary["end"]
    buf = io.BytesIO()
    W, H = landscape(A4)
    c = _canvas.Canvas(buf, pagesize=(W, H))
    text = pc.make_text_fn(c)
    m = 34

    state = {"y": 0.0, "page": 0}

    def new_page(subtitle: str = "") -> None:
        if state["page"]:
            c.showPage()
        state["page"] += 1
        text(m, H - 44, "米穀の出荷・販売に係る帳簿（食糧法第48条）", size=15)
        text(W - m, H - 42, f'{flags.get("business_name", "")}', size=11, align="right")
        text(m, H - 62,
             f"対象期間：{start:%Y年%m月%d日}〜{end:%Y年%m月%d日}（{fy_label(fy)}）", size=9)
        text(W - m, H - 62, f"作成日：{today():%Y年%m月%d日}", size=9, align="right")
        c.setLineWidth(0.8)
        c.line(m, H - 70, W - m, H - 70)
        state["y"] = H - 94
        if subtitle:
            text(m, state["y"], subtitle, size=11)
            state["y"] -= 18

    def need(h: float, subtitle: str = "") -> None:
        if state["y"] - h < 44:
            new_page(subtitle)

    def table(cols, widths, rows, size=8) -> None:
        """cols=見出し, widths=各列幅, rows=[[str,...]]（右詰めは数値っぽい列）。"""
        xs, x = [], m
        for wd in widths:
            xs.append(x)
            x += wd
        right = x

        def header():
            c.setLineWidth(0.6)
            c.line(m, state["y"] + 12, right, state["y"] + 12)
            for i, col in enumerate(cols):
                text(xs[i] + 2, state["y"], col, size=size)
            c.line(m, state["y"] - 5, right, state["y"] - 5)
            state["y"] -= 18

        header()
        for row in rows:
            if state["y"] < 44:
                new_page()
                header()
            for i, v in enumerate(row):
                s = str(v)
                if i and re.fullmatch(r"[-+]?[\d,]*\.?\d*", s.replace("—", "")) and s:
                    text(xs[i] + widths[i] - 4, state["y"], s, size=size, align="right")
                else:
                    text(xs[i] + 2, state["y"], s, size=size)
            state["y"] -= 13
        c.setLineWidth(0.4)
        c.line(m, state["y"] + 8, right, state["y"] + 8)
        state["y"] -= 14

    # --- 1ページ目：種類別数量と20トン判定 ---
    new_page("■ 種類別 数量（kg）　※種類＝うるち／もち、玄米／精米（農水省Q&A A12）")
    table(["種類", "期首在庫", "買受数量", "自家生産入庫", "とう精増減", "その他増減",
           "販売数量", "期末在庫(帳簿)", "期末在庫(実地)", "在庫数量", "差異"],
          [80, 68, 68, 72, 66, 66, 68, 78, 78, 68, 60],
          [[r["種類"]] + [fmt(r[c]) for c in SUMMARY_COLS[1:]] for r in summary["rows"]])

    j = summary["judge"]
    need(120)
    text(m, state["y"], "■ 20精米トン判定（法第47条／玄米量×0.91で精米換算）", size=11)
    state["y"] -= 18
    table(["項目", "精米換算kg"], [420, 120], [
        ["出荷・販売 合計", fmt(j["全体_精米換算kg"])],
        [f"うち除外：{EXCL_FREE}（Q&A A4）", fmt(j["除外_無償譲渡kg"])],
        [f"うち除外：{EXCL_TODOKEDE}（Q&A A8）", fmt(j["除外_届出事業者kg"])],
        ["判定対象", fmt(j["対象_精米換算kg"])],
    ], size=9)
    text(m, state["y"], f'判定対象：{j["対象_精米トン"]:.3f} 精米トン　→　{j["届出要否"]}', size=11)
    state["y"] -= 22
    for wmsg in summary["warnings"]:
        text(m, state["y"], f"※ {wmsg}", size=8)
        state["y"] -= 12

    # --- 買受・入庫・棚卸の記録 ---
    ent = [e for e in sorted(entries, key=lambda x: (str(x.get("entry_date") or ""),
                                                     x.get("id") or 0))
           if (parse_date(e.get("entry_date")) or date(1, 1, 1)) >= start
           and (parse_date(e.get("entry_date")) or date(9999, 12, 31)) <= end]
    new_page("■ 買受・入庫・棚卸の記録（手入力）")
    if ent:
        table(["日付", "区分", "うるち/もち", "玄米/精米", "数量kg", "とう精産出kg",
               "相手方", "備考"],
              [68, 104, 58, 58, 66, 72, 140, 208],
              [[f'{parse_date(e["entry_date"]):%Y/%m/%d}',
                ENTRY_KINDS.get(e.get("kind"), e.get("kind") or ""),
                e.get("rice_type") or "", e.get("form") or "",
                fmt(float(e.get("qty_kg") or 0)),
                fmt(float(e.get("qty_out_kg") or 0)) if e.get("kind") == "mill" else "",
                (e.get("counterparty") or "")[:16], (e.get("note") or "")[:24]]
               for e in ent])
    else:
        text(m, state["y"], "記録なし", size=9)
        state["y"] -= 16

    # --- 販売明細 ---
    new_page("■ 販売明細（注文データから自動集計）")
    if summary["details"]:
        table(["日付", "顧客", "商品", "個数", "うるち/もち", "玄米kg", "精米kg",
               "精米換算kg", "20トン判定", "除外理由"],
              [68, 130, 110, 40, 58, 62, 62, 74, 62, 108],
              [[f'{r["日付"]:%Y/%m/%d}', r["顧客"][:14], r["商品"][:12], str(r["個数"]),
                r["うるち/もち"], fmt(r["玄米kg"]), fmt(r["精米kg"]),
                fmt(r["精米換算kg"]), r["20トン判定"],
                _EXCL_SHORT.get(r["除外理由"], r["除外理由"])]
               for r in summary["details"]])
    else:
        text(m, state["y"], "該当なし", size=9)
        state["y"] -= 16

    c.showPage()
    c.save()
    return buf.getvalue()
