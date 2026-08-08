# -*- coding: utf-8 -*-
"""Shopify 連携（Admin API）。

BASEと同じ形：
1) 未発送の注文を取り込む（fetch_orders_via_api）… 支払済み・未発送の注文を
   商品(line_item)単位で orders テーブルへ追加する（精米量の集計に乗る）
2) 出荷確定時にShopify側へ発送完了＋伝票番号を反映する（dispatch_order）…
   Fulfillment Orders API 経由でクロネコヤマト・伝票番号を登録し、
   お客様への発送通知メールも自動送信される

認証情報は設定タブ（DB: shopify_config）に保存する：
  shop_domain … 例 "example.myshopify.com"
  access_token … カスタムアプリのAdmin APIアクセストークン（shpat_...）
必要なAPIスコープ：read_orders, read_fulfillments, write_fulfillments
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from . import base_api, db

API_VERSION = "2024-01"
YAMATO_TRACKING_COMPANY = "Yamato Transport"


def _cfg() -> dict:
    return db.get_setting("shopify_config") or {}


def _shop_domain() -> str:
    d = (_cfg().get("shop_domain") or "").strip()
    return d.replace("https://", "").replace("http://", "").rstrip("/")


def _token() -> str:
    return (_cfg().get("access_token") or "").strip()


def is_configured() -> bool:
    return bool(_shop_domain() and _token())


def _api_url(path: str) -> str:
    return f"https://{_shop_domain()}/admin/api/{API_VERSION}/{path}"


def _err_detail(e: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(e.read().decode())
        err = body.get("errors")
        return err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return f"HTTP {getattr(e, 'code', '?')}"


def _get(path: str, params: dict | None = None) -> dict:
    url = _api_url(path)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Shopify-Access-Token": _token()})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        _api_url(path), data=json.dumps(body).encode(), method="POST",
        headers={"X-Shopify-Access-Token": _token(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------------------
# 1) 未発送の注文を取り込む
# ---------------------------------------------------------------------------
def fetch_orders_via_api(limit: int = 100) -> dict:
    """支払済み・未発送(fulfillment_status=unfulfilled)の注文を商品単位で取り込む。"""
    if not is_configured():
        return {"added": 0, "skipped": 0,
                "error": "Shopify連携が未設定です（設定タブで登録してください）"}
    try:
        data = _get("orders.json", {
            "status": "open", "fulfillment_status": "unfulfilled",
            "financial_status": "paid", "limit": limit,
        })
    except urllib.error.HTTPError as e:
        return {"added": 0, "skipped": 0, "error": f"Shopify APIエラー：{_err_detail(e)}"}
    except Exception as e:  # noqa: BLE001
        return {"added": 0, "skipped": 0, "error": f"Shopify接続エラー：{e}"}

    orders = data.get("orders", [])
    norm = []
    for o in orders:
        oid = o.get("id")
        addr = o.get("shipping_address") or {}
        cust = o.get("customer") or {}
        last = addr.get("last_name") or cust.get("last_name") or ""
        first = addr.get("first_name") or cust.get("first_name") or ""
        name = f"{last}　{first}".strip("　 ") or (addr.get("name") or "")
        tel = addr.get("phone") or cust.get("phone") or o.get("phone") or ""
        zipc = (addr.get("zip") or "").replace("-", "").strip()
        address = f"{addr.get('province') or ''}{addr.get('city') or ''}{addr.get('address1') or ''}"
        address2 = addr.get("address2") or ""
        order_date = (o.get("created_at") or "")[:10].replace("-", "/") \
            or date.today().strftime("%Y/%m/%d")
        note = o.get("note") or ""

        for it in o.get("line_items", []):
            fulfillable = int(it.get("fulfillable_quantity") or 0)
            if fulfillable <= 0:
                continue
            iid = it.get("id")
            norm.append({
                "external_id": f"shopify:{oid}:{iid}",
                "order_date": order_date,
                "name": name, "kana": "", "zip": zipc,
                "address": address, "address2": address2, "tel": tel,
                "product": it.get("title") or "商品",
                "qty": fulfillable,
                "note": note,
                "dispatch_ref": json.dumps({"order_id": oid, "line_item_id": iid}),
            })

    result = base_api._save_orders(norm, channel="shopify")
    result["read"] = len(orders)
    return result


# ---------------------------------------------------------------------------
# 2) 出荷確定時：Shopify側を発送完了にする
# ---------------------------------------------------------------------------
def _find_fulfillment_order_line_item(order_id, line_item_id):
    """対象line_itemを含む、まだ発送可能なfulfillment orderを探す。

    returns (fulfillment_order_id, fulfillment_order_line_item_id, quantity) / (None, None, None)
    """
    data = _get(f"orders/{order_id}/fulfillment_orders.json")
    for fo in data.get("fulfillment_orders", []):
        if fo.get("status") not in ("open", "in_progress", "scheduled"):
            continue
        for li in fo.get("line_items", []):
            if li.get("line_item_id") == line_item_id:
                qty = li.get("fulfillable_quantity") or li.get("quantity") or 1
                return fo["id"], li["id"], qty
    return None, None, None


def dispatch_order(order_row) -> tuple[bool, str]:
    """Shopifyの1商品(line_item)をクロネコヤマト＋伝票番号で発送完了にする。

    - 配送業者：クロネコヤマト（Yamato Transport）を指定
    - 伝票番号：order_row['tracking_no'] を自動入力
    - お客様への発送通知メールも自動送信される（notify_customer=True）
    returns (成功, メッセージ)
    """
    if not is_configured():
        return False, "Shopify連携未設定（設定タブで連携してください）"
    ref = order_row.get("dispatch_ref") or ""
    try:
        info = json.loads(ref) if ref else {}
    except (json.JSONDecodeError, TypeError):
        info = {}
    order_id, line_item_id = info.get("order_id"), info.get("line_item_id")
    if not (order_id and line_item_id):
        return False, "発送対象の商品情報が未取得（Shopify取込をやり直してください）"

    tracking = re.sub(r"[^0-9A-Za-z]", "", str(order_row.get("tracking_no") or ""))
    try:
        fo_id, fol_id, qty = _find_fulfillment_order_line_item(order_id, line_item_id)
        if not fo_id:
            return False, "対象の商品は既に発送済み、または見つかりませんでした"
        fulfillment: dict = {
            "line_items_by_fulfillment_order": [{
                "fulfillment_order_id": fo_id,
                "fulfillment_order_line_items": [{"id": fol_id, "quantity": qty}],
            }],
            "notify_customer": True,
        }
        if tracking:
            fulfillment["tracking_info"] = {
                "number": tracking, "company": YAMATO_TRACKING_COMPANY,
                "url": f"https://member.kms.kuronekoyamato.co.jp/parcel/detail?pinCd={tracking}",
            }
        _post("fulfillments.json", {"fulfillment": fulfillment})
        if tracking:
            return True, f"Shopify発送完了（クロネコヤマト・伝票番号 {tracking}）"
        return True, "Shopify発送完了"
    except urllib.error.HTTPError as e:
        return False, f"Shopify発送失敗：{_err_detail(e)}"
    except Exception as e:  # noqa: BLE001
        return False, f"Shopify発送失敗：{e}"
