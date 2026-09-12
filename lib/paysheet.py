# -*- coding: utf-8 -*-
"""「お支払いのご案内」PDF（Squareの決済QRを載せた1枚もの・A5）。

未入金の相手に手渡す紙。QRを読めばその場でカード決済でき、振込にしたい
相手のために口座も併記する。領収書とは別書類にしている（領収書は入金済みの
証明なので、支払いを促すQRが同居すると書類として矛盾するため）。
"""
from __future__ import annotations

import io
import uuid
from datetime import date, datetime

from reportlab.lib.pagesizes import A5
from reportlab.pdfbase import pdfmetrics

from . import db
from . import pdf_common as pc

QR_SIZE = 120   # pt（紙面で約4.2cm）。スマホのカメラが少し離れても読める大きさ
HISTORY_KEY = "paysheet_history"   # settingsテーブル。発行した紙の一覧（新しい順）


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


# ---------------------------------------------------------------------------
# 発行履歴
# ---------------------------------------------------------------------------
# PDF本体は保存せず、作り直しに必要な値だけ残す。同じ値とURLから作れば
# 同じ紙になるうえ、1件数十KBのPDFをsettingsの1行に溜め込まずに済むため。

def get_history() -> list[dict]:
    """発行履歴（新しい順）。"""
    return db.get_setting(HISTORY_KEY) or []


def add_history(invoice_to: str, item_name: str, amount: int, issue_date: date,
                link: dict, note: str = "", show_bank: bool = True) -> dict:
    """発行した紙を履歴の先頭に追加して、その記録を返す。"""
    rec = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "issue_date": issue_date.isoformat(),
        "invoice_to": invoice_to, "item_name": item_name, "amount": int(amount),
        "url": link["url"], "link_id": link.get("id", ""),
        "order_id": link.get("order_id", ""),
        "note": note, "show_bank": show_bank,
        "paid": False, "paid_at": "", "paid_amount": 0, "checked_at": "",
    }
    hist = db.get_setting_live(HISTORY_KEY) or []   # 書き戻すので最新値から
    hist.insert(0, rec)
    db.set_setting(HISTORY_KEY, hist)
    return rec


def refresh_payments() -> int:
    """未入金の紙だけSquareに問い合わせて入金状況を更新する。更新件数を返す。

    入金済みは確定なので問い合わせない（件数が増えても呼び出しが増えないように）。
    """
    from .square_pay import payment_states

    hist = db.get_setting_live(HISTORY_KEY) or []
    pending = [r["order_id"] for r in hist if not r.get("paid") and r.get("order_id")]
    if not pending:
        return 0
    states = payment_states(pending)
    now = datetime.now().isoformat(timespec="seconds")
    changed = 0
    for r in hist:
        st = states.get(r.get("order_id", ""))
        if r.get("paid") or st is None:
            continue
        r["checked_at"] = now
        r["state"] = st["state"]
        if st["paid"]:
            r.update(paid=True, paid_at=st["paid_at"], paid_amount=st["paid_amount"])
            changed += 1
    db.set_setting(HISTORY_KEY, hist)
    return changed


def rebuild_pdf(rec: dict) -> tuple[bytes, str]:
    """履歴の記録から同じ紙を作り直して (PDF, ファイル名) を返す。"""
    pdf = build_pay_sheet_pdf(
        invoice_to=rec["invoice_to"], item_name=rec["item_name"], amount=rec["amount"],
        pay_url=rec["url"], issue_date=date.fromisoformat(rec["issue_date"]),
        note=rec.get("note", ""), show_bank=rec.get("show_bank", True))
    return pdf, pay_sheet_filename(rec["issue_date"], rec["invoice_to"])
