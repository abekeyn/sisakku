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
  access_token … Admin APIアクセストークン（shpat_...）
  client_id / client_secret … Shopify Dev DashboardのアプリのクライアントID／シークレット
    （OAuthでaccess_tokenを取得するために使う。取得後は不要だが残しておいて再連携に使う）
必要なAPIスコープ：read_orders, read_fulfillments, write_fulfillments と、発送完了の登録に必要な
  read_merchant_managed_fulfillment_orders, write_merchant_managed_fulfillment_orders
  （Fulfillment Orders API。これが無いと出荷確定で「権限なし(403)」になる）

access_tokenの取得はOAuth（アプリのURL＝このアプリ自身）で行う。設定タブで
shop_domain・client_id・client_secretを保存して「Shopifyと連携する」を押すと、
Shopifyの許可画面へ進み、戻ってきたところ（Home.pyの_shopify_oauth_gate）で
コードをaccess_tokenに交換してDBへ保存する。一度取得すれば以後は自動更新不要
（オフラインアクセストークンは期限切れしない）。
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from . import base_api, db, logic

API_VERSION = "2024-01"
YAMATO_TRACKING_COMPANY = "Yamato Transport"
AUTH_SCOPES = ("read_orders,read_fulfillments,write_fulfillments,"
               "read_merchant_managed_fulfillment_orders,write_merchant_managed_fulfillment_orders")


def _cfg() -> dict:
    return db.get_setting("shopify_config") or {}


def _shop_domain() -> str:
    d = (_cfg().get("shop_domain") or "").strip()
    return d.replace("https://", "").replace("http://", "").rstrip("/")


def _token() -> str:
    return (_cfg().get("access_token") or "").strip()


def is_configured() -> bool:
    return bool(_shop_domain() and _token())


def shop_domain() -> str:
    """設定済みのショップドメイン（画面側からの参照用）。"""
    return _shop_domain()


# ---------------------------------------------------------------------------
# OAuth（access_tokenの取得。アプリのURL＝このアプリ自身をコールバック先にする）
# ---------------------------------------------------------------------------
def oauth_ready() -> bool:
    """連携を開始できる（shop_domain・client_id・client_secretが揃っている）か。"""
    cfg = _cfg()
    return bool(_shop_domain() and cfg.get("client_id") and cfg.get("client_secret"))


def authorize_url(shop: str, redirect_uri: str, state: str) -> str:
    """Shopifyの許可画面のURL。ここへ飛ばすとマーチャントが許可→redirect_uriへ戻る。"""
    cfg = _cfg()
    q = urllib.parse.urlencode({
        "client_id": cfg.get("client_id", ""), "scope": AUTH_SCOPES,
        "redirect_uri": redirect_uri, "state": state,
    })
    return f"https://{shop}/admin/oauth/authorize?{q}"


def verify_hmac(params: dict) -> bool:
    """Shopifyから来たリクエストか検証する（クエリのhmacをクライアントシークレットで再計算）。"""
    secret = (_cfg().get("client_secret") or "").strip()
    sent = params.get("hmac", "")
    if not secret or not sent:
        return False
    msg = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "hmac")
    calc = _hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return _hmac.compare_digest(calc, sent)


def exchange_code(shop: str, code: str) -> tuple[bool, str]:
    """認可コードをaccess_tokenに交換し、shopify_configへ保存する。returns (成功, メッセージ)。"""
    cfg = _cfg()
    body = json.dumps({
        "client_id": cfg.get("client_id", ""), "client_secret": cfg.get("client_secret", ""),
        "code": code,
    }).encode()
    req = urllib.request.Request(
        f"https://{shop}/admin/oauth/access_token", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return False, f"トークン取得に失敗：{_err_detail(e)}"
    except Exception as e:  # noqa: BLE001
        return False, f"トークン取得に失敗：{e}"
    token = res.get("access_token")
    if not token:
        return False, "トークンが取得できませんでした"
    cfg["shop_domain"] = shop
    cfg["access_token"] = token
    db.set_setting("shopify_config", cfg)
    return True, "Shopifyと連携しました"


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
# Shopifyの province は「Ōsaka」「Hyōgo」のようにローマ字で来る。送り状(B2)は日本語の
# 都道府県が必須なので、province_code(JP-27)から日本語に直す。
_PREFECTURES = (
    "北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 "
    "神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 "
    "大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 "
    "福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県").split()


def _prefecture(addr: dict) -> str:
    m = re.match(r"JP-(\d{2})$", addr.get("province_code") or "")
    if m and 1 <= int(m.group(1)) <= 47:
        return _PREFECTURES[int(m.group(1)) - 1]
    prov = addr.get("province") or ""
    return prov if re.search(r"[぀-ヿ一-鿿]", prov) else ""


def _norm_phone(raw: str) -> str:
    """+818094764670 → 08094764670、ハイフン・空白を除去。"""
    d = re.sub(r"[^0-9+]", "", raw or "")
    if d.startswith("+81"):
        d = "0" + d[3:]
    return d.lstrip("+")


def _product_name(it: dict) -> str:
    """商品(line_item)から、精米量の集計に乗る品名を作る。

    Shopifyの商品名は「【予約】琥珀米 コシヒカリ」のように重さが入らず、重さ・精米方法は
    バリエーション（例「白米 / 5kg」）にある。商品名だけで取り込むと「精米不要・0kg」の
    商品として登録され、精米量に載らなかったため、BASEと同じくバリエーションから
    「精米5kg」「玄米5kg」等を作る。バリエーションが無い琥珀米【七日御膳】は専用名にする。
    """
    title = it.get("title") or "商品"
    name = base_api._product_from_choice(title, [it.get("variant_title") or ""])
    if name == title and "七日御膳" in title:
        return "琥珀米 七日御膳"
    return name


def _ensure_product(name: str, grams) -> None:
    """未登録の琥珀米系の商品は、精米が必要な商品として登録しておく（琥珀米は精米して出荷する）。

    既にある商品は触らない（マスタで直した内容を上書きしないため）。
    """
    if name.startswith(("精米", "玄米")):
        return
    if any(logic.normalize_text(p["name"]) == logic.normalize_text(name)
           for p in db.list_products(active_only=False)):
        return
    kg = round(float(grams or 0) / 1000, 3)
    db.upsert_product({
        "name": name, "category": "精米" if kg else "その他", "weight_kg": kg,
        "needs_milling": 1 if kg else 0, "yamato_name": name, "sort_order": 999, "active": 1,
    })



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
        billing = o.get("billing_address") or {}
        tel = _norm_phone(addr.get("phone") or billing.get("phone") or cust.get("phone")
                          or (cust.get("default_address") or {}).get("phone") or o.get("phone") or "")
        zipc = (addr.get("zip") or "").replace("-", "").strip()
        address = f"{_prefecture(addr)}{addr.get('city') or ''}{addr.get('address1') or ''}"
        address2 = addr.get("address2") or ""
        order_date = (o.get("created_at") or "")[:10].replace("-", "/") \
            or date.today().strftime("%Y/%m/%d")
        note = o.get("note") or ""

        for it in o.get("line_items", []):
            fulfillable = int(it.get("fulfillable_quantity") or 0)
            if fulfillable <= 0:
                continue
            iid = it.get("id")
            pname = _product_name(it)
            _ensure_product(pname, it.get("grams"))
            norm.append({
                "external_id": f"shopify:{oid}:{iid}",
                "order_date": order_date,
                "name": name, "kana": "", "zip": zipc,
                "address": address, "address2": address2, "tel": tel,
                "product": pname,
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
