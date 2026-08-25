# -*- coding: utf-8 -*-
"""領収書PDFの生成。

請求書と違い、LibreOffice（GitHub Actions経由）を使わずアプリ内で完結して
即時発行できるようにするため、reportlabで直接PDFを描画する。
日本語は Noto Sans JP（TrueType・templates/fonts/に同梱）を埋め込むため、
サーバー側にCJKフォントが無くても文字化け・空白表示にならない
（reportlab標準のCID日本語フォントは非埋め込みのため、閲覧環境によっては
表示されないことがある。実際に検証して埋め込み方式に切り替えた）。
"""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_PATH = Path(__file__).resolve().parent.parent / "templates" / "fonts" / "NotoSansJP-Regular.ttf"
FONT_NAME = "NotoSansJP"

_registered = False


def _ensure_font() -> None:
    global _registered
    if not _registered:
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
        _registered = True


# 阿部農園の発行者情報（請求書テンプレート templates/granada_invoice_template.xlsx と同一内容）
ISSUER_NAME = "阿部農園"
ISSUER_ZIP_ADDRESS = "〒963-0211　福島県郡山市片平町字西大町一"
ISSUER_TEL = "TEL：080-6030-3705"
ISSUER_REG_NO = "登録番号：T3810553743686"
ISSUER_CONTACT = "担当：阿部　喜臣"

TAX_RATE = 0.08


def build_receipt_pdf(invoice_to: str, amount: int, item_desc: str,
                      issue_date: date | None = None, doc_no: str = "") -> bytes:
    """領収書PDF(bytes)を作る。amountは税込金額。"""
    from reportlab.pdfgen import canvas as _canvas

    _ensure_font()
    issue_date = issue_date or date.today()
    tax_excl = round(amount / (1 + TAX_RATE))
    tax = amount - tax_excl

    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    F = FONT_NAME

    def text(x, y, s, size=11, align="left"):
        c.setFont(F, size)
        if align == "center":
            c.drawCentredString(x, y, s)
        elif align == "right":
            c.drawRightString(x, y, s)
        else:
            c.drawString(x, y, s)

    text(w / 2, h - 80, "領　収　書", size=26, align="center")

    text(w - 60, h - 130, f"発行日：{issue_date.strftime('%Y年%m月%d日')}", size=10, align="right")
    if doc_no:
        text(w - 60, h - 148, f"書類番号：{doc_no}", size=10, align="right")

    atesaki = f"{invoice_to}　御中"
    text(60, h - 180, atesaki, size=15)
    c.line(60, h - 186, 60 + max(240, pdfmetrics.stringWidth(atesaki, F, 15) + 10), h - 186)

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

    iy2 = 160
    text(w - 60, iy2 + 60, ISSUER_NAME, size=13, align="right")
    text(w - 60, iy2 + 42, ISSUER_ZIP_ADDRESS, size=9, align="right")
    text(w - 60, iy2 + 28, ISSUER_TEL, size=9, align="right")
    text(w - 60, iy2 + 14, ISSUER_REG_NO, size=9, align="right")
    text(w - 60, iy2, ISSUER_CONTACT, size=9, align="right")

    c.showPage()
    c.save()
    return buf.getvalue()


def receipt_filename(issue_date_iso: str, client_name: str = "") -> str:
    d = issue_date_iso.replace("-", "")
    return f"{d}_{client_name}_領収書.pdf" if client_name else f"{d}_領収書.pdf"
