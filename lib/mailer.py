# -*- coding: utf-8 -*-
"""PDFをメール添付で送る（Epson Connect「メールプリント」への送付用）。

クラウド（このアプリ）で発行した送り状PDFをプリンタのメールプリント宛先へ送ると、
PCが無くてもプリンタが自動で印刷する。

設定（.streamlit/secrets.toml / Streamlit Cloud secrets / 環境変数）:
    SMTP_HOST    例: smtp.gmail.com
    SMTP_PORT    例: 587（STARTTLS）/ 465（SSL）
    SMTP_USER    送信元メールアドレス（例: Gmail）
    SMTP_PASS    アプリパスワード
    MAIL_FROM    省略時は SMTP_USER
    PRINT_EMAIL  プリンタのメールプリント宛先（設定画面でも登録可）
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from . import config, db


# DBの mail_config（設定画面で入力）優先、無ければ secrets を使う
_DB_KEYMAP = {"SMTP_HOST": "host", "SMTP_PORT": "port", "SMTP_USER": "user",
              "SMTP_PASS": "password", "MAIL_FROM": "from"}


def _cfg(key: str, default: str = "") -> str:
    mc = db.get_setting("mail_config") or {}
    if key in _DB_KEYMAP and mc.get(_DB_KEYMAP[key]):
        return str(mc[_DB_KEYMAP[key]]).strip()
    v = config.get_secret(key, default)
    return v.strip() if isinstance(v, str) else (v or default)


def printer_email() -> str:
    """プリンタの宛先。設定画面(DB)を優先し、無ければ secrets を使う。"""
    s = db.get_setting("print_email")
    return (s or _cfg("PRINT_EMAIL", "")).strip()


def is_configured() -> tuple[bool, str]:
    """送信に必要な設定が揃っているか。(ok, 不足の説明)"""
    miss = [k for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS") if not _cfg(k)]
    if not printer_email():
        miss.append("プリンタ宛先(PRINT_EMAIL)")
    if miss:
        return False, "未設定: " + " / ".join(miss)
    return True, "OK"


def _send(to_addr: str, subject: str, body: str,
          pdf_bytes: bytes | None = None, filename: str = "soujou.pdf") -> tuple[bool, str]:
    host = _cfg("SMTP_HOST")
    port = int(_cfg("SMTP_PORT", "587") or "587")
    user = _cfg("SMTP_USER")
    pw = _cfg("SMTP_PASS")
    sender = _cfg("MAIL_FROM") or user
    if not (host and user and pw):
        return False, "メール送信設定（SMTP_*）が未設定です"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = subject or " "
    msg.set_content(body or " ")
    if pdf_bytes is not None:
        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf",
                           filename=filename)
    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=40) as s:
                s.login(user, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=40) as s:
                s.starttls(context=ctx)
                s.login(user, pw)
                s.send_message(msg)
        return True, "送信しました"
    except Exception as e:  # noqa: BLE001
        return False, f"メール送信に失敗: {e}"


def send_pdf_to_printer(pdf_bytes: bytes, filename: str = "soujou.pdf",
                        subject: str = "soujou") -> tuple[bool, str]:
    """送り状PDFをプリンタのメールプリント宛先へ送る。"""
    ok, why = is_configured()
    if not ok:
        return False, f"メール印刷の設定が未完了です（{why}）"
    to = printer_email()
    # 本文は最小限（Epson側で本文も印刷される設定だと余白ページが出るため）
    ok2, msg = _send(to, subject, " ", pdf_bytes=pdf_bytes, filename=filename)
    return (ok2, f"プリンタへPDFを送信しました（{to}）" if ok2 else msg)


def send_test(to_addr: str = "") -> tuple[bool, str]:
    """接続テスト。宛先未指定なら自分（SMTP_USER）宛に送り、SMTP設定を検証する。"""
    to = (to_addr or _cfg("SMTP_USER")).strip()
    if not to:
        return False, "送信先がありません（SMTP_USER 未設定）"
    ok, msg = _send(to, "【テスト】精米・発送管理 メール設定確認",
                    "このメールが届けば、メール送信設定は正常です。", pdf_bytes=None)
    return (ok, f"テストメールを送信しました（{to}）。受信を確認してください。" if ok else msg)
