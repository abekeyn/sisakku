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
    "cash": "現金にてご入金いただきました。",
}

# 印紙税法上、現金の受取書は税抜記載金額が5万円以上で課税文書（収入印紙が必要）になる。
# 振込・その他（現金の授受を伴わない）は対象外。
STAMP_DUTY_THRESHOLD = 50_000


def build_receipt_pdf(invoice_to: str, amount: int, item_desc: str,
                      issue_date: date | None = None, doc_no: str = "",
                      payment_method: str = "bank", payment_note: str = "") -> bytes:
    """領収書PDF(bytes)を作る（A5サイズ）。amountは税込金額。

    payment_method: "bank"(振込・既定) / "cash"(現金) / "other"(自由記述)。
    "other" のときは payment_note の内容をそのまま注記として使う。
    """
    from reportlab.pdfgen import canvas as _canvas

    pc.ensure_font()
    issue_date = issue_date or date.today()
    tax_excl = round(amount / (1 + pc.TAX_RATE))
    tax = amount - tax_excl

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

    atesaki = f"{invoice_to}　御中"
    text(m, h - 118, atesaki, size=13)
    c.line(m, h - 123, m + max(200, pdfmetrics.stringWidth(atesaki, pc.FONT_NAME, 13) + 10), h - 123)

    box_y = h - 165
    c.rect(m, box_y - 9, w - 2 * m, 38, stroke=1, fill=0)
    text(m + 14, box_y + 5, "ご請求金額", size=10)
    text(w - m - 14, box_y + 5, f"¥ {amount:,} －", size=16, align="right")

    text(m, box_y - 32, f"但し　{item_desc} として", size=9.5)
    text(m, box_y - 48, "上記正に領収いたしました。", size=9.5)

    iy = box_y - 78
    text(m, iy, "【内訳】", size=8.5)
    text(m + 14, iy - 15, f"税抜金額　¥{tax_excl:,}", size=8.5)
    text(m + 14, iy - 29, f"消費税等（8%）　¥{tax:,}", size=8.5)
    if note:
        text(m, iy - 47, f"※{note}", size=7)
    if needs_stamp:
        text(m, iy - 59, "※現金でのご入金・税抜5万円以上のため、収入印紙を貼付しております。",
            size=7)

    pc.draw_issuer_block(c, w, 60)

    c.showPage()
    c.save()
    return buf.getvalue()


def receipt_filename(issue_date_iso: str, client_name: str = "") -> str:
    d = issue_date_iso.replace("-", "")
    return f"{d}_{client_name}_領収書.pdf" if client_name else f"{d}_領収書.pdf"
