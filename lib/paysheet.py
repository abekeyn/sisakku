# -*- coding: utf-8 -*-
"""「お支払いのご案内」PDF（Squareの決済QRを載せた1枚もの・A5）。

未入金の相手に手渡す紙。QRを読めばその場でカード決済でき、振込にしたい
相手のために口座も併記する。領収書とは別書類にしている（領収書は入金済みの
証明なので、支払いを促すQRが同居すると書類として矛盾するため）。
"""
from __future__ import annotations

import io
from datetime import date

from reportlab.lib.pagesizes import A5
from reportlab.pdfbase import pdfmetrics

from . import pdf_common as pc

QR_SIZE = 120   # pt（紙面で約4.2cm）。スマホのカメラが少し離れても読める大きさ


def build_pay_sheet_pdf(invoice_to: str, item_name: str, amount: int,
                        pay_url: str, issue_date: date | None = None,
                        note: str = "", show_bank: bool = True) -> bytes:
    """お支払いのご案内PDF(bytes)を作る（A5・1枚）。amountは税込金額。"""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as _canvas

    from .square_pay import qr_png

    pc.ensure_font()
    issue_date = issue_date or date.today()
    tax_excl = round(amount / (1 + pc.TAX_RATE))
    tax = amount - tax_excl

    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=A5)
    w, h = A5
    text = pc.make_text_fn(c)
    m = 34

    text(w / 2, h - 46, "お支払いのご案内", size=18, align="center")
    text(w - m, h - 72, f"発行日：{issue_date.strftime('%Y年%m月%d日')}", size=8,
         align="right")

    if invoice_to:
        atesaki = f"{invoice_to}　御中"
        text(m, h - 104, atesaki, size=13)
        c.line(m, h - 109,
               m + max(200, pdfmetrics.stringWidth(atesaki, pc.FONT_NAME, 13) + 10),
               h - 109)

    box_y = h - 152
    c.rect(m, box_y - 9, w - 2 * m, 38, stroke=1, fill=0)
    text(m + 14, box_y + 5, "お支払い金額", size=10)
    text(w - m - 14, box_y + 5, f"¥ {amount:,} －", size=16, align="right")

    text(m, box_y - 30, f"品名　{item_name}", size=9.5)
    text(m, box_y - 44, f"（税抜 ¥{tax_excl:,}　消費税等8% ¥{tax:,}）", size=8)

    # QRは中央に大きく。読み取りが主目的の紙なので最優先で目に入る位置に置く。
    qx = (w - QR_SIZE) / 2
    qy = box_y - 84 - QR_SIZE
    text(w / 2, qy + QR_SIZE + 18, "▼ スマートフォンのカメラで読み取ってください",
         size=9.5, align="center")
    c.drawImage(ImageReader(io.BytesIO(qr_png(pay_url))), qx, qy, QR_SIZE, QR_SIZE,
                mask="auto")
    text(w / 2, qy - 14, "クレジットカード・Google Pay等でお支払いいただけます。",
         size=8.5, align="center")
    text(w / 2, qy - 26, pay_url, size=7, align="center")

    y = qy - 48
    if show_bank:
        text(m, y, "■ お振込みをご希望の場合", size=9)
        text(m + 12, y - 14, pc.BANK_INFO, size=8.5)
        text(m + 12, y - 27, f"口座名義　{pc.BANK_HOLDER}", size=8.5)
        text(m + 12, y - 40, "※振込手数料はご負担をお願いいたします。", size=7)
        y -= 56
    if note:
        text(m, y, f"※{note}", size=7.5)

    pc.draw_issuer_block(c, w, 60)

    c.showPage()
    c.save()
    return buf.getvalue()


def pay_sheet_filename(issue_date_iso: str, client_name: str = "") -> str:
    d = issue_date_iso.replace("-", "")
    return (f"{d}_{client_name}_お支払いのご案内.pdf" if client_name
            else f"{d}_お支払いのご案内.pdf")
