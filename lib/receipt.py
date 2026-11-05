# -*- coding: utf-8 -*-
"""領収書PDFの生成（アプリ内で即時・reportlab・A5サイズ1枚に収める）。"""
from __future__ import annotations

import io
from datetime import date

from reportlab.lib.pagesizes import A5
from reportlab.pdfbase import pdfmetrics

from . import pdf_common as pc

# 入金方法ごとの定型注記。"other" は payment_note をそのまま使う。
PAYMENT_NOTES = {
    "bank": "お振込みにてご入金を確認しております（現金領収ではないため収入印紙は不要です）。",
    "cash": "現金にてお支払いいただきました。",
}

# 印紙税法上、現金の受取書は税抜記載金額が5万円以上で課税文書（収入印紙が必要）になる。
# 振込・その他（現金の授受を伴わない）は対象外。
STAMP_DUTY_THRESHOLD = 50_000


def build_receipt_pdf(invoice_to: str, amount: int, item_desc: str,
                      issue_date: date | None = None, doc_no: str = "",
                      payment_method: str = "bank", payment_note: str = "",
                      qty: float = 1, total_kg: float | None = None,
                      honorific: str = "御中",
                      tax_rows: list[tuple[int, int, int]] | None = None,
                      item_rows: list[dict] | None = None) -> bytes:
    """領収書PDF(bytes)を作る（A5サイズ）。amountは税込金額。

    item_descは「精米5kg」のような1個あたりの品名。qtyが1件でない場合、
    但し書きに「×個数（合計◯kg）」を付け、金額と数量が食い違って見えない
    ようにする（品名の"5kg"だけを見て全体の金額だと誤解されるのを防ぐ）。
    payment_method: "bank"(振込・既定) / "cash"(現金) / "other"(自由記述)。
    "other" のときは payment_note の内容をそのまま注記として使う。
    honorific: 宛名の敬称（"御中"＝既定・会社宛　／　"様"＝個人宛）。
    tax_rows: 税率が混ざる請求（複数商品）の内訳 [(税率, 税抜金額, 消費税)]。無ければ8%で計算する。
    item_rows: 複数商品の明細 [{"name", "qty", "rate", "amount"(税込)}]。渡すと、但し書きの下に
    1行ずつ載せる（請求額どおりの領収のときだけ渡す。行が多いときは末尾を「ほか◯品目」にまとめる）。

    支払い用のQRはここには刷らない（領収書は入金済みの証明なので、支払いを
    促すQRが同居すると書類として矛盾する）。未入金の相手に渡す紙は
    paysheet.build_pay_sheet_pdf() で別に作る。
    """
    from reportlab.pdfgen import canvas as _canvas

    pc.ensure_font()
    issue_date = issue_date or date.today()
    if tax_rows:
        tax_excl = sum(r[1] for r in tax_rows)
        tax = sum(r[2] for r in tax_rows)
    else:
        tax_excl = round(amount / (1 + pc.TAX_RATE))
        tax = amount - tax_excl

    desc = item_desc
    if qty and qty != 1:
        kg_note = f"　合計{total_kg:g}kg" if total_kg else ""
        desc = f"{item_desc}　×{qty:g}{kg_note}"

    note = payment_note.strip() if payment_method == "other" else PAYMENT_NOTES.get(
        payment_method, PAYMENT_NOTES["bank"])
    needs_stamp = payment_method == "cash" and tax_excl >= STAMP_DUTY_THRESHOLD

    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=A5)
    w, h = A5
    text = pc.make_text_fn(c)
    m = 34  # 左右余白

    text(w / 2, h - 46, "領　収　書", size=19, align="center")

    text(w - m, h - 74, f"発行日：{issue_date.strftime('%Y年%m月%d日')}", size=8, align="right")
    if doc_no:
        text(w - m, h - 87, f"書類番号：{doc_no}", size=8, align="right")

    if needs_stamp:
        # 収入印紙貼付欄（現金・税抜5万円以上）。実際の印紙は印刷後に手貼りする。
        sw, sh = 46, 46
        sx, sy = m, h - 46 - sh
        c.rect(sx, sy, sw, sh, stroke=1, fill=0)
        text(sx + sw / 2, sy + sh / 2 + 2, "収入印紙", size=7, align="center")
        text(sx + sw / 2, sy + sh / 2 - 9, "貼付欄", size=7, align="center")

    atesaki = f"{invoice_to}　{honorific}"
    text(m, h - 118, atesaki, size=13)
    c.line(m, h - 123, m + max(200, pdfmetrics.stringWidth(atesaki, pc.FONT_NAME, 13) + 10), h - 123)

    box_y = h - 165
    c.rect(m, box_y - 9, w - 2 * m, 38, stroke=1, fill=0)
    text(m + 14, box_y + 5, "ご請求金額", size=10)
    text(w - m - 14, box_y + 5, f"¥ {amount:,} －", size=16, align="right")

    tadashi = f"但し　{desc} として"
    if pdfmetrics.stringWidth(tadashi, pc.FONT_NAME, 9.5) > w - 2 * m:
        text(m, box_y - 32, f"但し　{desc}", size=9.5)
        text(m, box_y - 46, "として上記正に領収いたしました。", size=9.5)
    else:
        text(m, box_y - 32, tadashi, size=9.5)
        text(m, box_y - 46, "上記正に領収いたしました。", size=9.5)

    iy = box_y - 78
    if item_rows:
        # 明細表。下の【内訳】（税）と発行者欄に重ならない範囲で、行の高さを詰める
        y0 = box_y - 64
        extra_rate = 14 * (len(tax_rows) - 1) if (tax_rows and len(tax_rows) > 1) else 0
        min_iy = 205 + extra_rate + (12 if needs_stamp else 0)
        avail = (y0 - 5) - (min_iy + 20)
        n = len(item_rows)
        rh = min(15.0, avail / n)
        shown, more = item_rows, 0
        if rh < 11:
            cap = max(1, int(avail // 11) - 1)
            shown, more, rh = item_rows[:cap], n - cap, 11.0
        fs = 8.5 if rh >= 13 else 7.5
        text(m, y0, "品名", size=8)
        text(w - m - 128, y0, "数量", size=8, align="right")
        text(w - m - 78, y0, "税率", size=8, align="right")
        text(w - m, y0, "金額(税込)", size=8, align="right")
        c.line(m, y0 - 5, w - m, y0 - 5)
        y = y0 - 5
        name_w = w - 2 * m - 150
        for r in shown:
            y -= rh
            text(m, y, _clip(str(r["name"]), name_w, fs), size=fs)
            text(w - m - 128, y, str(r.get("qty", "")), size=fs, align="right")
            text(w - m - 78, y, f"{r['rate']}%", size=fs, align="right")
            text(w - m, y, f"¥{int(r['amount']):,}", size=fs, align="right")
        if more:
            y -= rh
            text(m, y, f"ほか{more}品目（詳細は請求書のとおり）", size=fs)
        c.line(m, y - 5, w - m, y - 5)
        iy = y - 5 - 20
    text(m, iy, "【内訳】", size=8.5)
    text(m + 14, iy - 15, f"税抜金額　¥{tax_excl:,}", size=8.5)
    if tax_rows and len(tax_rows) > 1:
        extra = 0
        for rate, excl, tx in tax_rows:
            text(m + 14, iy - 29 - extra, f"消費税等（{rate}%）　対象 ¥{excl:,}　税額 ¥{tx:,}", size=8.5)
            extra += 14
        extra -= 14
    elif tax_rows:
        text(m + 14, iy - 29, f"消費税等（{tax_rows[0][0]}%）　¥{tax:,}", size=8.5)
        extra = 0
    else:
        text(m + 14, iy - 29, f"消費税等（8%）　¥{tax:,}", size=8.5)
        extra = 0
    if note:
        text(m, iy - 47 - extra, f"※{note}", size=7)
    if needs_stamp:
        text(m, iy - 59 - extra, "※現金でのご入金・税抜5万円以上のため、収入印紙を貼付しております。",
            size=7)

    pc.draw_issuer_block(c, w, 60)

    c.showPage()
    c.save()
    return buf.getvalue()


def _clip(s: str, max_w: float, size: float) -> str:
    """max_wに収まるよう、長い品名の末尾を「…」で切る。"""
    if pdfmetrics.stringWidth(s, pc.FONT_NAME, size) <= max_w:
        return s
    while s and pdfmetrics.stringWidth(s + "…", pc.FONT_NAME, size) > max_w:
        s = s[:-1]
    return s + "…"


def receipt_filename(issue_date_iso: str, client_name: str = "") -> str:
    d = issue_date_iso.replace("-", "")
    return f"{d}_{client_name}_領収書.pdf" if client_name else f"{d}_領収書.pdf"
