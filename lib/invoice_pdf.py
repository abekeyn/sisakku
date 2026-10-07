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
    c = _canvas.Canvas(buf, pagesize=A5)
    w, h = A5
    text = pc.make_text_fn(c)
    m = 34  # 左右余白（領収書と同じ）

    text(w / 2, h - 46, "御　請　求　書", size=19, align="center")

    text(w - m, h - 74, f"発行日：{issue_date.strftime('%Y年%m月%d日')}", size=8, align="right")
    text(w - m, h - 87, f"書類番号：{doc_no}", size=8, align="right")

    atesaki = f"{invoice_to}　御中"
    text(m, h - 118, atesaki, size=13)
    c.line(m, h - 123, m + max(200, pdfmetrics.stringWidth(atesaki, pc.FONT_NAME, 13) + 10), h - 123)

    text(m, h - 146, "件名：お米代", size=8.5)
    bank = f"振込先：{pc.BANK_INFO}"
    text(m, h - 159, bank, size=8.5)
    text(m, h - 172, f"　　　　{pc.BANK_HOLDER}", size=8.5)

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


def invoice_filename(issue_date: date, client_name: str = "") -> str:
    d = issue_date.strftime("%Y%m%d")
    return f"{d}_{client_name}_請求書.pdf" if client_name else f"{d}_請求書.pdf"
