# -*- coding: utf-8 -*-
"""Squareの決済リンク発行とQRコード生成。

領収書・請求書に「スマホで読み取ればカードで払える」QRを載せるための部品。
金額と品名を渡すと Square Checkout API で決済リンクを作り、そのURLを
QRコード画像（PNG）にして返す。

リンクは発行のたびに必ず新しく作る。APIで作った決済リンクは1回しか
支払えない（Square公式: "The buyer can use the payment link only once."）
ため、同じ品名・金額で使い回すと、先に誰かが払った後の紙が「払えないQR」
になってしまう。1枚1リンクにしておけば、リンクごとの注文(order)の状態で
その紙が入金済みかどうかも判定できる。

必要なシークレット（.streamlit/secrets.toml もしくは環境変数）:
  SQUARE_ACCESS_TOKEN … Square開発者ダッシュボードで発行する本番アクセストークン
  SQUARE_LOCATION_ID  … 店舗ID（阿部農園）。未設定ならトークンから自動取得する。
"""
from __future__ import annotations

import io
import uuid
from . import config

API_BASE = "https://connect.squareup.com/v2"
# Square APIはバージョン固定が必須。上げるときは実機で決済画面まで確認すること。
API_VERSION = "2026-08-19"
CURRENCY = "JPY"          # 円は最小単位が1円なので、金額はそのままの整数で渡す
TIMEOUT = 20


class SquareError(RuntimeError):
    """Square APIが期待どおりに応答しなかったとき。文面はそのまま画面に出す。"""


def access_token() -> str:
    return (config.get_secret("SQUARE_ACCESS_TOKEN") or "").strip()


def location_id() -> str:
    return (config.get_secret("SQUARE_LOCATION_ID") or "").strip()


def is_configured() -> bool:
    """QR発行に必要なシークレットが揃っているか。"""
    return bool(access_token())


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {access_token()}",
        "Square-Version": API_VERSION,
        "Content-Type": "application/json",
    }


def _api_error(resp) -> str:
    """Squareのエラー応答を、画面にそのまま出せる1行にまとめる。"""
    try:
        errs = resp.json().get("errors") or []
    except ValueError:
        errs = []
    if errs:
        return "／".join(f"{e.get('code', '')}: {e.get('detail', '')}".strip(": ")
                         for e in errs)
    return f"HTTP {resp.status_code}"


def resolve_location_id() -> str:
    """店舗IDを返す。未設定なら Locations API から最初の有効な店舗を拾う。"""
    if lid := location_id():
        return lid
    import requests
    resp = requests.get(f"{API_BASE}/locations", headers=_headers(), timeout=TIMEOUT)
    if not resp.ok:
        raise SquareError(f"店舗情報を取得できませんでした（{_api_error(resp)}）")
    for loc in resp.json().get("locations", []):
        if loc.get("status") == "ACTIVE":
            return loc["id"]
    raise SquareError("有効な店舗が見つかりませんでした。SQUARE_LOCATION_IDを設定してください。")


def create_payment_link(name: str, amount: int, note: str = "") -> dict:
    """Squareに決済リンクを新規作成して {"url", "id", "name", "amount"} を返す。

    配送先住所は集めない（ask_for_shipping_address=False）。自分で届ける卸や
    店頭手渡しでは住所入力が純粋な手間にしかならないため。
    """
    import requests

    name = (name or "").strip()
    amount = int(amount)
    if not name:
        raise SquareError("品名が空です。")
    if amount <= 0:
        raise SquareError("金額は1円以上を指定してください。")

    body = {
        "idempotency_key": str(uuid.uuid4()),
        "quick_pay": {
            "name": name,
            "price_money": {"amount": amount, "currency": CURRENCY},
            "location_id": resolve_location_id(),
        },
        "checkout_options": {
            "allow_tipping": False,
            "ask_for_shipping_address": False,
        },
    }
    if note:
        body["payment_note"] = note[:500]

    resp = requests.post(f"{API_BASE}/online-checkout/payment-links",
                         headers=_headers(), json=body, timeout=TIMEOUT)
    if not resp.ok:
        raise SquareError(f"決済リンクを作成できませんでした（{_api_error(resp)}）")
    link = resp.json().get("payment_link") or {}
    url = link.get("url")
    if not url:
        raise SquareError("決済リンクのURLが応答に含まれていませんでした。")
    return {"url": url, "id": link.get("id", ""), "order_id": link.get("order_id", ""),
            "name": name, "amount": amount}


def link_order_id(link_id: str) -> str:
    """決済リンクIDから、そのリンクに紐づく注文IDを引く（履歴の補完用）。"""
    import requests
    resp = requests.get(f"{API_BASE}/online-checkout/payment-links/{link_id}",
                        headers=_headers(), timeout=TIMEOUT)
    if not resp.ok:
        raise SquareError(f"決済リンクを取得できませんでした（{_api_error(resp)}）")
    return (resp.json().get("payment_link") or {}).get("order_id", "")


def payment_states(order_ids: list[str]) -> dict[str, dict]:
    """注文IDごとの入金状況を {order_id: {"paid", "state", "paid_at", "paid_amount"}} で返す。

    決済リンクの注文は、払われると DRAFT→OPEN に変わり Tender（支払い記録）が
    付く。state だけでなく Tender の有無で判定するのは、手動で完了扱いにされた
    未入金の注文を「入金済み」と誤表示しないため。
    """
    import requests

    ids = [i for i in dict.fromkeys(order_ids) if i]
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 100):          # batch-retrieve は1回100件まで
        resp = requests.post(f"{API_BASE}/orders/batch-retrieve", headers=_headers(),
                             json={"order_ids": ids[i:i + 100]}, timeout=TIMEOUT)
        if not resp.ok:
            raise SquareError(f"入金状況を取得できませんでした（{_api_error(resp)}）")
        for o in resp.json().get("orders", []):
            tenders = o.get("tenders") or []
            out[o["id"]] = {
                "state": o.get("state", ""),
                "paid": bool(tenders) and o.get("state") in ("OPEN", "COMPLETED"),
                "paid_at": min((t.get("created_at", "") for t in tenders), default=""),
                "paid_amount": sum((t.get("amount_money") or {}).get("amount", 0)
                                   for t in tenders),
            }
    return out


def qr_png(url: str, box_size: int = 10) -> bytes:
    """URLをQRコードのPNG(bytes)にする。

    誤り訂正レベルHは、印刷物が多少かすれても読めるようにするため。
    """
    import qrcode
    from qrcode.constants import ERROR_CORRECT_H

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=box_size, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return buf.getvalue()
