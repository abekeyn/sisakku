# -*- coding: utf-8 -*-
"""Squareの決済リンク発行とQRコード生成。

領収書・請求書に「スマホで読み取ればカードで払える」QRを載せるための部品。
金額と品名を渡すと Square Checkout API で決済リンクを作り、そのURLを
QRコード画像（PNG）にして返す。

同じ品名・同じ金額のリンクは作り直さず使い回す（DBのsettingsにキャッシュ）。
発行のたびに新しいリンクが増えると、Square側の管理画面が使い捨てリンクで
埋まって「どれが生きているのか」が分からなくなるため。

必要なシークレット（.streamlit/secrets.toml もしくは環境変数）:
  SQUARE_ACCESS_TOKEN … Square開発者ダッシュボードで発行する本番アクセストークン
  SQUARE_LOCATION_ID  … 店舗ID（阿部農園）。未設定ならトークンから自動取得する。
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime

from . import config, db

API_BASE = "https://connect.squareup.com/v2"
# Square APIはバージョン固定が必須。上げるときは実機で決済画面まで確認すること。
API_VERSION = "2026-08-19"
CURRENCY = "JPY"          # 円は最小単位が1円なので、金額はそのままの整数で渡す
LINKS_KEY = "square_payment_links"   # settingsテーブルのキャッシュキー
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


def _cache() -> dict:
    return db.get_setting(LINKS_KEY) or {}


def _cache_key(name: str, amount: int) -> str:
    return f"{name.strip()}|{int(amount)}"


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
    return {"url": url, "id": link.get("id", ""), "name": name, "amount": amount}


def get_payment_link(name: str, amount: int, note: str = "") -> dict:
    """品名・金額に対応する決済リンクを返す（あれば使い回し、無ければ作成）。"""
    key = _cache_key(name, amount)
    cache = _cache()
    if hit := cache.get(key):
        if hit.get("url"):
            return hit
    made = create_payment_link(name, amount, note=note)
    made["created_at"] = datetime.now().isoformat(timespec="seconds")
    cache[key] = made
    db.set_setting(LINKS_KEY, cache)
    return made


def forget_payment_link(name: str, amount: int) -> None:
    """キャッシュを捨てて、次回また新しいリンクを作らせる。"""
    cache = _cache()
    if cache.pop(_cache_key(name, amount), None) is not None:
        db.set_setting(LINKS_KEY, cache)


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
