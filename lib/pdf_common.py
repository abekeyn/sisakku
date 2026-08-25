# -*- coding: utf-8 -*-
"""請求書・領収書PDF共通部品（reportlab・日本語フォント埋め込み）。

LibreOffice（GitHub Actions経由）を使わず、アプリ内で完結して即時にPDFを
作れるようにするための共通ヘルパー。日本語は Noto Sans JP（TrueType・
templates/fonts/に同梱）を埋め込むため、サーバー側にCJKフォントが無くても
文字化け・空白表示にならない（reportlab標準のCID日本語フォントは非埋め込み
のため、閲覧環境によっては表示されないことを実機検証で確認して埋め込み
方式にした）。
"""
from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_PATH = Path(__file__).resolve().parent.parent / "templates" / "fonts" / "NotoSansJP-Regular.ttf"
FONT_NAME = "NotoSansJP"

_registered = False


def ensure_font() -> None:
    global _registered
    if not _registered:
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
        _registered = True


# 阿部農園の発行者情報（templates/granada_invoice_template.xlsx と同一内容）
ISSUER_NAME = "阿部農園"
ISSUER_ZIP_ADDRESS = "〒963-0211　福島県郡山市片平町字西大町一"
ISSUER_TEL = "TEL：080-6030-3705"
ISSUER_REG_NO = "登録番号：T3810553743686"
ISSUER_CONTACT = "担当：阿部　喜臣"
BANK_INFO = "七十七銀行郡山支店　普通　5025573"
BANK_HOLDER = "阿部 喜臣（アベ ヨシタカ）"

TAX_RATE = 0.08


def make_text_fn(c):
    """canvas向けのテキスト描画ヘルパー（left/center/right揃え対応）を返す。"""
    def text(x, y, s, size=11, align="left"):
        c.setFont(FONT_NAME, size)
        if align == "center":
            c.drawCentredString(x, y, s)
        elif align == "right":
            c.drawRightString(x, y, s)
        else:
            c.drawString(x, y, s)
    return text


def draw_issuer_block(c, w, base_y) -> None:
    """右下に発行者情報（阿部農園）をまとめて描画する。"""
    text = make_text_fn(c)
    text(w - 60, base_y + 60, ISSUER_NAME, size=13, align="right")
    text(w - 60, base_y + 42, ISSUER_ZIP_ADDRESS, size=9, align="right")
    text(w - 60, base_y + 28, ISSUER_TEL, size=9, align="right")
    text(w - 60, base_y + 14, ISSUER_REG_NO, size=9, align="right")
    text(w - 60, base_y, ISSUER_CONTACT, size=9, align="right")
