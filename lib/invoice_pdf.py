# -*- coding: utf-8 -*-
"""請求書PDFの生成（アプリ内で即時・reportlab）。

毎月末日の自動請求（lib/billing.pyのprepare_all）は、実際の会計帳簿と
書式を合わせるため引き続き templates/*.xlsx ＋ LibreOffice(GitHub Actions)
で作る。こちらは「手入力での新規作成」「数量・単価の修正」など、その場で
即時にPDFが欲しい操作向け（LibreOffice不要・待ち時間なし）。
"""
from __future__ import annotations

import io
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics

from . import pdf_common as pc


def build_invoice_pdf(invoice_to: str, item_desc: str, qty: float, unit_price: float,
                      issue_date: date, doc_no, amount: int | None = None) -> bytes:
    """請求書PDF(bytes)を作る。qtyは個数(1個あたりの重量はunit_price側で決まる)。

    amountを指定すると、税込合計をその金額に固定する（端数調整）。その場合、
    明細行の単価・金額(税抜)は数量×単価のまま表示しつつ、消費税等の行で
    差額を吸収して合計と一致させる（税抜金額合計＋消費税等＝税込合計は常に成立）。
    """
    from reportlab.pdfgen import canvas as _canvas

    pc.ensure_font()
    unit_excl = round(unit_price / (1 + pc.TAX_RATE))
    line_excl = round(unit_excl * qty)
    total = amount if amount is not None else round(qty * unit_price)
    tax = total - line_excl

    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    text = pc.make_text_fn(c)

    text(w / 2, h - 70, "御 請 求 書", size=24, align="center")

    text(w - 60, h - 120, f"発行日：{issue_date.strftime('%Y年%m月%d日')}", size=10, align="right")
    text(w - 60, h - 136, f"書類番号：{doc_no}", size=10, align="right")

    atesaki = f"{invoice_to}　御中"
    text(60, h - 150, atesaki, size=15)
    c.line(60, h - 156, 60 + max(240, pdfmetrics.stringWidth(atesaki, pc.FONT_NAME, 15) + 10), h - 156)

    text(60, h - 186, "件名：お米代", size=10)
    text(60, h - 202, f"振込先：{pc.BANK_INFO}", size=10)
    text(60, h - 216, f"　　　　{pc.BANK_HOLDER}", size=10)

    box_y = h - 280
    c.rect(60, box_y - 10, w - 120, 46, stroke=1, fill=0)
    text(80, box_y + 8, "合計金額（税込）", size=12)
    text(w - 80, box_y + 8, f"¥ {total:,} －", size=20, align="right")

    # 明細
    ty = box_y - 55
    c.setFont(pc.FONT_NAME, 9)
    text(60, ty, "内容", size=9)
    text(340, ty, "数量(個)", size=9)
    text(400, ty, "単価(税抜)", size=9)
    text(468, ty, "税率", size=9)
    text(w - 60, ty, "金額(税抜)", size=9, align="right")
    c.line(60, ty - 6, w - 60, ty - 6)

    ty -= 24
    text(60, ty, item_desc, size=10)
    text(345, ty, f"{qty:g}", size=10)
    text(400, ty, f"¥{unit_excl:,}", size=10)
    text(468, ty, "8%", size=10)
    text(w - 60, ty, f"¥{line_excl:,}", size=10, align="right")
    c.line(60, ty - 10, w - 60, ty - 10)

    ty -= 34
    text(400, ty, "税抜金額合計", size=9)
    text(w - 60, ty, f"¥{line_excl:,}", size=10, align="right")
    ty -= 16
    text(400, ty, "消費税等（8%）", size=9)
    text(w - 60, ty, f"¥{tax:,}", size=10, align="right")
    ty -= 16
    text(400, ty, "税込合計", size=9)
    text(w - 60, ty, f"¥{total:,}", size=10, align="right")

    pc.draw_issuer_block(c, w, 160)

    c.showPage()
    c.save()
    return buf.getvalue()


def invoice_filename(issue_date: date, client_name: str = "") -> str:
    d = issue_date.strftime("%Y%m%d")
    return f"{d}_{client_name}_請求書.pdf" if client_name else f"{d}_請求書.pdf"
