# -*- coding: utf-8 -*-
"""請求書PDFの生成（アプリ内で即時・reportlab・領収書と同じA5サイズ）。

毎月末日の自動請求（lib/billing.pyのprepare_all）は、実際の会計帳簿と
書式を合わせるため引き続き templates/*.xlsx ＋ LibreOffice(GitHub Actions)
で作る。こちらは「手入力での新規作成」「数量・単価の修正」など、その場で
即時にPDFが欲しい操作向け（LibreOffice不要・待ち時間なし）。
"""
from __future__ import annotations

import io
from datetime import date

from reportlab.lib.pagesizes import A5
from reportlab.pdfbase import pdfmetrics

from . import pdf_common as pc


# ===== 複数商品（明細行）=====================================================
_KG_UNITS = ("kg", "㎏", "KG", "Kg", "キロ")
RATES = (8, 10)           # 軽減税率（お米など）／標準税率（送料・資材など）


def clean_items(items) -> list[dict]:
    """明細行を正規化する。品名が空・数量が0以下の行は捨てる。

    kg は「1単位あたりの重さ（kg）」。在庫の出庫量と納品書の合計kgに使う。
    単位が kg の行は、空欄なら 1 とみなす（数量がそのままkgになる）。
    """
    out = []
    for it in items or []:
        name = str(it.get("name") or "").strip()
        try:
            qty = float(it.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if not name or qty <= 0:
            continue
        unit = str(it.get("unit") or "").strip()
        try:
            kg = float(it.get("kg") or 0)
        except (TypeError, ValueError):
            kg = 0.0
        if kg <= 0 and unit in _KG_UNITS:
            kg = 1.0
        try:
            price = float(it.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        rate = int(it.get("rate") or 8)
        out.append({"name": name, "qty": qty, "unit": unit, "price": price,
                    "rate": rate if rate in RATES else 8, "kg": max(kg, 0.0)})
    return out


def item_calc(items) -> dict:
    """明細の各行と、税率別・全体の金額（税込の単価×数量を税込金額とする）。

    丸め方は1行請求書・見積書と同じ：税込金額＝round(税込単価×数量)、
    税抜金額＝round(税抜単価×数量)。税率別の消費税＝税込小計−税抜小計。
    """
    rows, by_rate = [], {}
    for it in clean_items(items):
        unit_excl = round(it["price"] / (1 + it["rate"] / 100))
        line_excl = round(unit_excl * it["qty"])
        line_incl = round(it["price"] * it["qty"])
        rows.append({**it, "unit_excl": unit_excl, "line_excl": line_excl, "line_incl": line_incl})
        g = by_rate.setdefault(it["rate"], {"excl": 0, "incl": 0})
        g["excl"] += line_excl
        g["incl"] += line_incl
    for g in by_rate.values():
        g["tax"] = g["incl"] - g["excl"]
    excl = sum(g["excl"] for g in by_rate.values())
    total = sum(g["incl"] for g in by_rate.values())
    return {"rows": rows, "by_rate": dict(sorted(by_rate.items())), "excl": excl,
            "tax": total - excl, "total": total,
            "kg": sum(r["qty"] * r["kg"] for r in rows)}


def items_summary(items) -> str:
    """領収書の但し書きなどに使う短い品名（例：精米5kg ほか2品目）。"""
    rows = clean_items(items)
    if not rows:
        return ""
    return rows[0]["name"] + (f" ほか{len(rows) - 1}品目" if len(rows) > 1 else "")


def _qty_text(r: dict) -> str:
    return f"{r['qty']:g}{r['unit']}"


def _build_items_pdf(invoice_to: str, items, issue_date: date, doc_no,
                     doc_type: str, honorific: str) -> bytes:
    """複数商品の請求書・納品書。行が多いときは続きのページへ送り、合計は最後のページに置く。"""
    from reportlab.pdfgen import canvas as _canvas

    pc.ensure_font()
    calc = item_calc(items)
    rows = calc["rows"]
    is_dn = doc_type == "納品書"

    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=A5)
    w, h = A5
    text = pc.make_text_fn(c)
    m = 34

    text(w / 2, h - 46, "納　品　書" if is_dn else "御　請　求　書", size=19, align="center")
    text(w - m, h - 74, f"{'納品日' if is_dn else '発行日'}：{issue_date.strftime('%Y年%m月%d日')}",
         size=8, align="right")
    text(w - m, h - 87, f"書類番号：{doc_no}", size=8, align="right")
    atesaki = f"{invoice_to}　{honorific}"
    text(m, h - 118, atesaki, size=13)
    c.line(m, h - 123, m + max(200, pdfmetrics.stringWidth(atesaki, pc.FONT_NAME, 13) + 10), h - 123)

    if is_dn:
        text(m, h - 146, "件名：お米の納品", size=8.5)
        text(m, h - 159, "下記の通り納品いたしました。ご確認をお願いいたします。", size=8.5)
        first_y = h - 215                      # 表の見出し行
        name_w = w - 2 * m - 90
    else:
        text(m, h - 146, "件名：お米代", size=8.5)
        text(m, h - 159, f"振込先：{pc.BANK_INFO}", size=8.5)
        text(m, h - 172, f"　　　　{pc.BANK_HOLDER}", size=8.5)
        box_y = h - 215
        c.rect(m, box_y - 9, w - 2 * m, 38, stroke=1, fill=0)
        text(m + 14, box_y + 5, "合計金額（税込）", size=10)
        text(w - m - 14, box_y + 5, f"¥ {calc['total']:,} －", size=16, align="right")
        first_y = box_y - 48
        name_w = 212 - 8 - m

    fs, lead = 9, 11
    row_h = 16

    # 合計欄の高さ（最後のページに必ず収まるよう、行の置き場の下限に足す）
    multi_rate = len(calc["by_rate"]) > 1
    if is_dn:
        totals_h = 40
    else:
        n_lines = len(calc["by_rate"]) * (2 if multi_rate else 1) + 1   # 税率ごとの行＋税込合計
        totals_h = 14 + 15 * (1 + n_lines)
    floor = 140 + totals_h                      # 140＝右下の発行者欄（発行者名の行）の上端

    plan = []
    for r in rows:
        nl = _wrap(r["name"], pc.FONT_NAME, fs, name_w)
        plan.append({"r": r, "name": nl, "h": row_h + (len(nl) - 1) * lead})
    # 途中のページは下まで使う（発行者欄・合計欄が無いため）。最後のページだけ、合計欄の分を
    # 空けておく。入りきらなければ最後の行を次のページへ送る。
    mid_floor = 70
    pages, cur, y = [], [], first_y - 22
    for pr in plan:
        if cur and y - pr["h"] < mid_floor:
            pages.append(cur)
            cur, y = [], h - 90 - 22
        cur.append(pr)
        y -= pr["h"]
    pages.append(cur)

    def _room_ok(page_rows: list, is_first: bool) -> bool:
        top = (first_y - 22) if is_first else (h - 90 - 22)
        return top - sum(r["h"] for r in page_rows) >= floor

    while len(pages[-1]) > 1 and not _room_ok(pages[-1], len(pages) == 1):
        pages.append([pages[-1].pop()])

    def header(y0: float) -> None:
        text(m, y0, "内容", size=8.5)
        if is_dn:
            text(w - m, y0, "数量", size=8.5, align="right")
        else:
            text(212, y0, "数量", size=8.5)
            text(256, y0, "単価(税抜)", size=8.5)
            text(312, y0, "税率", size=8.5)
            text(w - m, y0, "金額(税抜)", size=8.5, align="right")
        c.line(m, y0 - 6, w - m, y0 - 6)

    ty = first_y
    for i, page_rows in enumerate(pages):
        if i:
            c.showPage()
            ty = h - 90
            text(m, ty + 24, f"{invoice_to}　{honorific}　／　書類番号：{doc_no}（続き）", size=8)
        header(ty)
        ty -= 22
        for pr in page_rows:
            r = pr["r"]
            for k, ln in enumerate(pr["name"]):
                text(m, ty - k * lead, ln, size=fs)
            if is_dn:
                text(w - m, ty, _qty_text(r), size=fs, align="right")
            else:
                text(214, ty, _qty_text(r), size=fs)
                text(256, ty, f"¥{r['unit_excl']:,}", size=fs)
                text(312, ty, f"{r['rate']}%", size=fs)
                text(w - m, ty, f"¥{r['line_excl']:,}", size=fs, align="right")
            ty -= pr["h"]
    c.line(m, ty + row_h - 9, w - m, ty + row_h - 9)

    ty -= 12
    if is_dn:
        if calc["kg"] > 0:
            text(w - m - 90, ty, "合計", size=9, align="right")
            text(w - m, ty, f"{calc['kg']:g} kg", size=11, align="right")
    else:
        text(256, ty, "税抜金額合計", size=8.5)
        text(w - m, ty, f"¥{calc['excl']:,}", size=9.5, align="right")
        for rate, g in calc["by_rate"].items():
            if multi_rate:
                ty -= 15
                text(256, ty, f"（{rate}%対象）税抜", size=8.5)
                text(w - m, ty, f"¥{g['excl']:,}", size=9.5, align="right")
            ty -= 15
            text(256, ty, f"消費税等（{rate}%）", size=8.5)
            text(w - m, ty, f"¥{g['tax']:,}", size=9.5, align="right")
        ty -= 15
        text(256, ty, "税込合計", size=8.5)
        text(w - m, ty, f"¥{calc['total']:,}", size=9.5, align="right")

    pc.draw_issuer_block(c, w, 60)
    c.showPage()
    c.save()
    return buf.getvalue()


def _wrap(s: str, font: str, size: float, max_w: float) -> list[str]:
    """幅 max_w に収まるよう文字単位で折り返す（日本語は単語区切りが無いため）。"""
    lines, cur = [], ""
    for ch in str(s):
        if cur and pdfmetrics.stringWidth(cur + ch, font, size) > max_w:
            lines.append(cur)
            cur = ch.lstrip()
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines or [""]


def build_invoice_pdf(invoice_to: str, item_desc: str, qty: float, unit_price: float,
                      issue_date: date, doc_no, amount: int | None = None,
                      doc_type: str = "請求書", honorific: str = "御中",
                      total_kg: float | None = None, items: list[dict] | None = None) -> bytes:
    """請求書PDF(bytes)を作る。qtyは個数(1個あたりの重量はunit_price側で決まる)。

    amountを指定すると、税込合計をその金額に固定する（端数調整）。その場合、
    明細行の単価・金額(税抜)は数量×単価のまま表示しつつ、消費税等の行で
    差額を吸収して合計と一致させる（税抜金額合計＋消費税等＝税込合計は常に成立）。

    doc_type="納品書" のときは、同じ様式で表題を「納品書」、日付を「納品日」にし、
    金額・単価・税の欄と支払いの案内（振込先）を外し、品名と数量(kg)だけを載せる
    （total_kg＝合計kg）。honorific は宛名の敬称（御中／様）。

    items を渡すと複数商品の明細で作る（各行：name・qty・unit・price〔税込〕・rate・kg）。
    税率は8%・10%を混ぜられ、税率ごとの対象額と消費税を載せる。items が無ければ従来の1行。
    """
    if clean_items(items):      # 複数商品（明細行）。無ければ従来の1行
        return _build_items_pdf(invoice_to, items, issue_date, doc_no, doc_type, honorific)

    from reportlab.pdfgen import canvas as _canvas

    pc.ensure_font()
    unit_excl = round(unit_price / (1 + pc.TAX_RATE))
    line_excl = round(unit_excl * qty)
    total = amount if amount is not None else round(qty * unit_price)
    tax = total - line_excl

    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=A5)
    w, h = A5
    text = pc.make_text_fn(c)
    m = 34  # 左右余白（領収書と同じ）

    is_dn = doc_type == "納品書"
    text(w / 2, h - 46, "納　品　書" if is_dn else "御　請　求　書", size=19, align="center")

    date_label = "納品日" if is_dn else "発行日"
    text(w - m, h - 74, f"{date_label}：{issue_date.strftime('%Y年%m月%d日')}", size=8, align="right")
    text(w - m, h - 87, f"書類番号：{doc_no}", size=8, align="right")

    atesaki = f"{invoice_to}　{honorific}"
    text(m, h - 118, atesaki, size=13)
    c.line(m, h - 123, m + max(200, pdfmetrics.stringWidth(atesaki, pc.FONT_NAME, 13) + 10), h - 123)

    if is_dn:
        text(m, h - 146, "件名：お米の納品", size=8.5)
        text(m, h - 159, "下記の通り納品いたしました。ご確認をお願いいたします。", size=8.5)
    else:
        text(m, h - 146, "件名：お米代", size=8.5)
        bank = f"振込先：{pc.BANK_INFO}"
        text(m, h - 159, bank, size=8.5)
        text(m, h - 172, f"　　　　{pc.BANK_HOLDER}", size=8.5)

    if is_dn:
        # 納品書：金額は載せず、品名と数量(kg)だけ
        kg = total_kg if total_kg is not None else qty
        ty = h - 215
        text(m, ty, "内容", size=8.5)
        text(w - m, ty, "数量(kg)", size=8.5, align="right")
        c.line(m, ty - 6, w - m, ty - 6)
        ty -= 22
        lines = _wrap(item_desc, pc.FONT_NAME, 9.5, w - 2 * m - 90)
        for i, ln in enumerate(lines):
            text(m, ty - i * 13, ln, size=9.5)
        text(w - m, ty, f"{kg:g} kg", size=9.5, align="right")
        ty -= (len(lines) - 1) * 13
        c.line(m, ty - 9, w - m, ty - 9)
        ty -= 30
        text(w - m - 90, ty, "合計", size=9, align="right")
        text(w - m, ty, f"{kg:g} kg", size=11, align="right")
        pc.draw_issuer_block(c, w, 60)
        c.showPage()
        c.save()
        return buf.getvalue()

    box_y = h - 215
    c.rect(m, box_y - 9, w - 2 * m, 38, stroke=1, fill=0)
    text(m + 14, box_y + 5, "合計金額（税込）", size=10)
    text(w - m - 14, box_y + 5, f"¥ {total:,} －", size=16, align="right")

    # 明細
    ty = box_y - 48
    text(m, ty, "内容", size=8.5)
    text(212, ty, "数量(個)", size=8.5)
    text(256, ty, "単価(税抜)", size=8.5)
    text(312, ty, "税率", size=8.5)
    text(w - m, ty, "金額(税抜)", size=8.5, align="right")
    c.line(m, ty - 6, w - m, ty - 6)

    # 内容欄は数量の列(x=212)の手前までしか使えない。長い品名は折り返して重ならないようにする。
    ty -= 22
    lines = _wrap(item_desc, pc.FONT_NAME, 9.5, 212 - 8 - m)
    for i, ln in enumerate(lines):
        text(m, ty - i * 13, ln, size=9.5)
    text(216, ty, f"{qty:g}", size=9.5)
    text(256, ty, f"¥{unit_excl:,}", size=9.5)
    text(312, ty, "8%", size=9.5)
    text(w - m, ty, f"¥{line_excl:,}", size=9.5, align="right")
    ty -= (len(lines) - 1) * 13
    c.line(m, ty - 9, w - m, ty - 9)

    ty -= 30
    text(256, ty, "税抜金額合計", size=8.5)
    text(w - m, ty, f"¥{line_excl:,}", size=9.5, align="right")
    ty -= 15
    text(256, ty, "消費税等（8%）", size=8.5)
    text(w - m, ty, f"¥{tax:,}", size=9.5, align="right")
    ty -= 15
    text(256, ty, "税込合計", size=8.5)
    text(w - m, ty, f"¥{total:,}", size=9.5, align="right")

    pc.draw_issuer_block(c, w, 60)

    c.showPage()
    c.save()
    return buf.getvalue()


def delivery_note_filename(issue_date: date, client_name: str = "") -> str:
    d = issue_date.strftime("%Y%m%d")
    return f"{d}_{client_name}_納品書.pdf" if client_name else f"{d}_納品書.pdf"


def invoice_filename(issue_date: date, client_name: str = "") -> str:
    d = issue_date.strftime("%Y%m%d")
    return f"{d}_{client_name}_請求書.pdf" if client_name else f"{d}_請求書.pdf"
