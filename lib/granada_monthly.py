# -*- coding: utf-8 -*-
"""鉄板焼きかいか（グラナダ）様 月次請求の完全自動ジョブ。

毎月末日に実行され、以下を一手に行う:
  1. ヤマトB2クラウドから当月の発行済データを取得
  2. 南志穂様宛の発送を集計（5kgあたり送料込み4000円）
  3. 請求書シートを追加し、当月分PDFを発行書類フォルダへ保存
  4. keiri@granada-jp.net へPDFを添付してメール送信
  5. 完了（または要確認）をスマホ(ntfy)へ通知

安全策:
  - 末日以外は実行しない（--force で上書き）
  - 当月の発送が0件なら送信せず「要確認」を通知（誤請求防止）
  - 送信済みマーカーで二重送信を防止
  - SMTP未設定ならPDFまで作成し「手動送信が必要」を通知

使い方:
  python -m lib.granada_monthly                 # 末日に当月分を自動実行・送信
  python -m lib.granada_monthly --dry-run       # 取得・集計・帳票まで（送信しない）
  python -m lib.granada_monthly --month 2026-07 --force --dry-run
"""
from __future__ import annotations

import argparse
import calendar
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import granada_invoice as gi  # noqa: E402

NTFY_URL = "https://ntfy.sh/abe-rice-farm-claude"
CUSTOMER_EMAIL = "keiri@granada-jp.net"
SENT_MARKER_DIR = gi.OUT_DIR


def notify(title_ascii: str, body_jp: str, priority: str = "default",
           tags: str = "rice") -> None:
    """スマホ(ntfy)へ通知。HTTPヘッダはlatin-1のみ可なので日本語は本文先頭へ。"""
    try:
        data = body_jp.encode("utf-8")
        req = urllib.request.Request(
            NTFY_URL, data=data,
            headers={"Title": title_ascii, "Priority": priority,
                     "Tags": tags, "Content-Type": "text/plain; charset=utf-8"},
            method="POST")
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:  # noqa: BLE001
        print("ntfy通知に失敗（処理は継続）:", e)


def _is_last_day(d: date) -> bool:
    return d.day == calendar.monthrange(d.year, d.month)[1]


def _smtp_send(to_addr: str, subject: str, body: str,
               pdf_bytes: bytes, filename: str) -> tuple[bool, str]:
    """secrets.toml の SMTP_* を直接読んで送信（DB非依存・無人実行向け）。"""
    import smtplib
    import ssl
    from email.message import EmailMessage
    from lib import config

    host = config.get_secret("SMTP_HOST", "smtp.gmail.com")
    port = int(config.get_secret("SMTP_PORT", "587") or 587)
    user = config.get_secret("SMTP_USER", "")
    pw = config.get_secret("SMTP_PASS", "")
    sender = config.get_secret("MAIL_FROM", "") or user
    if not (host and user and pw):
        return False, "SMTP未設定（.streamlit/secrets.toml の SMTP_USER / SMTP_PASS）"
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
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
        return True, f"送信しました（{to_addr}）"
    except Exception as e:  # noqa: BLE001
        return False, f"メール送信に失敗: {e}"


def _mail_body(year: int, month: int) -> tuple[str, str]:
    subject = f"阿部農園　{month}月分請求につきまして"
    body = (
        "株式会社グラナダ\n"
        "経理部\n"
        "ご担当者様\n\n"
        "いつもお世話になっております。\n"
        "阿部農園の阿部と申します。\n\n"
        f"{year}年{month}月分の請求書を送付いたします。\n"
        "内容についてご確認の上、\n"
        "ご不明点等ございましたらご返信をお願いいたします。\n\n"
        "以上、よろしくお願いいたします。\n"
    )
    return subject, body


def _fetch_issued_csv(target_ym: str) -> bytes:
    """ヤマトB2クラウドから target_ym を含む期間の発行済データ(CSV bytes)を取得。"""
    from lib import b2_fetch
    y, m = int(target_ym[:4]), int(target_ym[5:7])
    last = calendar.monthrange(y, m)[1]
    # 月初を確実に含むよう、末日からひと月分+α遡って取得
    days = last + 5
    raw = b2_fetch._download_issued(days=days)
    if raw is None:
        raise RuntimeError("ヤマトから発行済データを取得できませんでした")
    return raw


def run(target_ym: str | None = None, do_send: bool = True,
        force: bool = False, issued_csv_path: str | None = None,
        xlsx_path: Path | None = None, out_dir: Path | None = None,
        marker_dir: Path | None = None) -> dict:
    # パスは明示指定可（テスト時はscratchpadへ。既定は本番）
    xlsx_path = xlsx_path or gi.INVOICE_XLSX
    out_dir = out_dir or gi.OUT_DIR
    marker_dir = marker_dir or SENT_MARKER_DIR
    today = date.today()
    if target_ym is None:
        if _is_last_day(today):
            ty, tm = today.year, today.month               # 末日＝当月分
        elif today.day <= 3:                               # 末日にPCが落ちていた翌日等
            prev = today.replace(day=1) - timedelta(days=1)  # ＝前月分のキャッチアップ
            ty, tm = prev.year, prev.month
        else:
            ty, tm = today.year, today.month
        target_ym = f"{ty:04d}-{tm:02d}"
    y, m = int(target_ym[:4]), int(target_ym[5:7])

    # 実行ウィンドウ：末日、または月初1〜3日（取りこぼし救済）のみ
    if not force and not (_is_last_day(today) or today.day <= 3):
        print(f"本日({today})は実行日（末日/月初）ではないため実行しません（--forceで強制）")
        return {"skipped": "out_of_window"}

    # 1) ヤマト取得（ローカルCSV指定があればそれを使う＝テスト用）
    if issued_csv_path:
        raw = Path(issued_csv_path).read_bytes()
    else:
        raw = _fetch_issued_csv(target_ym)

    # 2) 集計
    s = gi.summarize_month(raw, target_ym)
    qty, total_kg, cnt = s["qty"], s["total_kg"], s["count"]
    print(f"[{target_ym}] 南志穂様宛 {cnt}件 / 計{total_kg:g}kg / 数量{qty:g}個 / "
          f"¥{int(round(qty*gi.PRICE_PER_UNIT)):,}")

    if cnt == 0 or qty <= 0:
        notify("Granada invoice: NEEDS CHECK",
               f"⚠️ {m}月分の南志穂様宛発送が0件でした。\n"
               f"請求書は作成・送信していません。手動でご確認ください。",
               priority="high", tags="warning")
        return {"target_ym": target_ym, "count": 0, "sent": False,
                "reason": "no_shipments"}

    # 3) 請求書シート＋PDF
    info = gi.generate(target_ym, qty, xlsx_path=xlsx_path, out_dir=out_dir)
    amount = info["amount"]
    pdf_path = Path(info["pdf_path"])
    print("PDF:", pdf_path, "doc", info["doc_number"])

    # 4) メール送信（冪等：送信済みマーカーで二重送信防止）
    marker = marker_dir / f".sent_{target_ym.replace('-', '')}"
    sent = False
    send_msg = ""
    if not do_send:
        send_msg = "（--dry-run のため送信しません）"
    elif marker.exists():
        send_msg = "既に送信済み（マーカーあり）のため再送しません"
    else:
        subject, body = _mail_body(y, m)
        ok, send_msg = _smtp_send(
            CUSTOMER_EMAIL, subject, body,
            pdf_bytes=pdf_path.read_bytes(), filename=pdf_path.name)
        sent = ok
        if ok:
            marker.write_text(
                f"{date.today().isoformat()} sent {amount} to {CUSTOMER_EMAIL}",
                encoding="utf-8")

    # 5) スマホ通知
    if sent:
        notify("Granada invoice: SENT",
               f"✅ {m}月分の請求書を送信しました。\n"
               f"金額 ¥{amount:,}（{qty:g}個/{total_kg:g}kg）\n"
               f"宛先 {CUSTOMER_EMAIL}\n書類番号 {info['doc_number']}",
               tags="white_check_mark")
    elif do_send:
        notify("Granada invoice: SEND FAILED",
               f"⚠️ {m}月分のPDFは作成しましたが送信できませんでした。\n"
               f"{send_msg}\nPDF: {pdf_path}\n手動送信をお願いします。",
               priority="high", tags="warning")
    else:
        notify("Granada invoice: DRAFT READY",
               f"📄 {m}月分のPDFを作成しました（未送信）。\n"
               f"金額 ¥{amount:,}（{qty:g}個）\nPDF: {pdf_path}",
               tags="page_facing_up")

    return {"target_ym": target_ym, "count": cnt, "total_kg": total_kg,
            "qty": qty, "amount": amount, "pdf_path": str(pdf_path),
            "doc_number": info["doc_number"], "sent": sent, "send_msg": send_msg}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="対象月 YYYY-MM（既定: 当月）")
    ap.add_argument("--dry-run", action="store_true", help="送信しない")
    ap.add_argument("--force", action="store_true", help="末日でなくても実行")
    ap.add_argument("--issued-csv", help="ヤマト取得の代わりにローカルCSVを使う")
    ap.add_argument("--check", action="store_true",
                    help="ヤマト取得＋集計の確認のみ（帳票作成・送信・通知なし）")
    a = ap.parse_args()
    if a.check:
        ym = a.month or date.today().strftime("%Y-%m")
        raw = (Path(a.issued_csv).read_bytes() if a.issued_csv
               else _fetch_issued_csv(ym))
        s = gi.summarize_month(raw, ym)
        print(f"[確認 {ym}] 南志穂様宛 {s['count']}件 / 計{s['total_kg']:g}kg / "
              f"数量{s['qty']:g}個 / ¥{int(round(s['qty']*gi.PRICE_PER_UNIT)):,}")
        for r in s["rows"]:
            print(f"   {r['date']} {r['hinmei']} ({r['kg']:g}kg) 伝票{r['tracking']}")
        return
    try:
        r = run(target_ym=a.month, do_send=not a.dry_run, force=a.force,
                issued_csv_path=a.issued_csv)
        print("結果:", r)
    except Exception as e:  # noqa: BLE001  無人実行：どんな失敗でも必ずスマホ通知
        import traceback
        traceback.print_exc()
        mlabel = a.month or date.today().strftime("%Y-%m")
        notify("Granada invoice: ERROR",
               f"❌ 月次請求の自動処理でエラーが発生しました。\n"
               f"対象: {mlabel}\n{type(e).__name__}: {e}\n"
               f"請求書は送信できていません。手動でご対応ください。",
               priority="urgent", tags="rotating_light")
        raise


if __name__ == "__main__":
    main()
