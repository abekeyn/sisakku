# -*- coding: utf-8 -*-
"""領収書PDFの生成（アプリ内で即時・reportlab）。"""
from __future__ import annotations

import io
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics

from . import pdf_common as pc


def build_receipt_pdf(invoice_to: str, amount: int, item_desc: str,
                      issue_date: date | None = None, doc_no: str = "") -> bytes:
    """領収書PDF(bytes)を作る。amountは税込金額。"""
    from reportlab.pdfgen import canvas as _canvas

    pc.ensure_font()
    issue_date = issue_date or date.today()
    tax_excl = round(amount / (1 + pc.TAX_RATE))
    tax = amount - tax_excl

    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    text = pc.make_text_fn(c)

    text(w / 2, h - 80, "領　収　書", size=26, align="center")

    text(w - 60, h - 130, f"発行日：{issue_date.strftime('%Y年%m月%d日')}", size=10, align="right")
    if doc_no:
        text(w - 60, h - 148, f"書類番号：{doc_no}", size=10, align="right")

    atesaki = f"{invoice_to}　御中"
    text(60, h - 180, atesaki, size=15)
    c.line(60, h - 186, 60 + max(240, pdfmetrics.stringWidth(atesaki, pc.FONT_NAME, 15) + 10), h - 186)

    box_y = h - 260
    c.rect(60, box_y - 10, w - 120, 46, stroke=1, fill=0)
    text(80, box_y + 8, "ご請求金額", size=12)
    text(w - 80, box_y + 8, f"¥ {amount:,} －", size=20, align="right")

    text(60, box_y - 40, f"但し　{item_desc} として", size=12)
    text(60, box_y - 60, "上記正に領収いたしました。", size=12)

    iy = box_y - 100
    text(60, iy, "【内訳】", size=10)
    text(80, iy - 18, f"税抜金額　¥{tax_excl:,}", size=10)
    text(80, iy - 34, f"消費税等（8%）　¥{tax:,}", size=10)
    text(80, iy - 54,
        "※お振込みにてご入金を確認しております（現金領収ではないため収入印紙は不要です）。",
        size=8)

    pc.draw_issuer_block(c, w, 160)

    c.showPage()
    c.save()
    return buf.getvalue()


def receipt_filename(issue_date_iso: str, client_name: str = "") -> str:
    d = issue_date_iso.replace("-", "")
    return f"{d}_{client_name}_領収書.pdf" if client_name else f"{d}_領収書.pdf"
