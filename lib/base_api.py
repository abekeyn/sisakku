# -*- coding: utf-8 -*-
"""BASE 連携。

2通りの取込に対応：
1) CSV取込  … BASE管理画面からダウンロードした注文CSVを読み込む（すぐ使える）
2) API取込  … 認証情報(リフレッシュトークン)があればAPIで自動取得

いずれも共通の正規化フォーマットに変換し、orders テーブルへ追加する。
"""
from __future__ import annotations

import csv
import io
import json
import urllib.parse
import urllib.request
from datetime import date

from . import db, logic

BASE_API = "https://api.thebase.in/1"


# ---------------------------------------------------------------------------
# 共通：正規化された注文を orders テーブルへ追加（重複は external_id で防止）
# ---------------------------------------------------------------------------
def _save_orders(norm_orders: list[dict], channel: str = "base") -> dict:
    added, skipped = 0, 0
    for o in norm_orders:
        if o.get("external_id") and db.order_exists(o["external_id"]):
            skipped += 1
            continue
        cust = {
            "name": o.get("name", ""),
            "kana": o.get("kana", ""),
            "tel": o.get("tel", ""),
            "zip": o.get("zip", ""),
            "address": o.get("address", ""),
            "address2": o.get("address2", ""),
            "company": o.get("company", ""),
            "honorific": "様",
        }
        cid = db.upsert_customer(cust)
        pid = logic.match_or_create_product(o.get("product", "") or "商品")
        db.add_order({
            "customer_id": cid,
            "product_id": pid,
            "qty": int(o.get("qty", 1) or 1),
            "channel": channel,
            "order_date": o.get("order_date", date.today().isoformat()),
            "ship_date": "",
            "delivery_date": "",
            "delivery_time": "",
            "milling_kg_override": None,
            "note": o.get("note", ""),
            "status": "pending",
            "external_id": o.get("external_id", ""),
            "dispatch_ref": o.get("dispatch_ref", ""),
        })
        added += 1
    return {"added": added, "skipped": skipped}


# ---------------------------------------------------------------------------
# 1) CSV取込
# ---------------------------------------------------------------------------
# BASEの注文CSVは列名が変わることがあるため、候補名で柔軟に対応する
_CANDIDATES = {
    "external_id": ["注文ID", "受注番号", "注文番号", "order_id", "オーダーID"],
    "order_date": ["注文日", "注文日時", "受注日", "購入日"],
    "name": ["氏名", "お名前", "購入者氏名", "配送先氏名", "宛名", "name"],
    "kana": ["フリガナ", "ふりがな", "カナ", "氏名カナ"],
    "zip": ["郵便番号", "〒", "postal", "zip"],
    "address": ["住所", "配送先住所", "address"],
    "address2": ["建物名", "建物", "マンション"],
    "tel": ["電話番号", "TEL", "tel", "phone"],
    "product": ["商品名", "品名", "item", "product"],
    "qty": ["数量", "個数", "点数", "quantity", "qty"],
    "company": ["会社名", "法人名", "company"],
    "note": ["備考", "通信欄", "メモ"],
}


def _find_col(header: list[str], keys: list[str]) -> int | None:
    norm = [logic.normalize_text(h) for h in header]
    for k in keys:
        nk = logic.normalize_text(k)
        for i, h in enumerate(norm):
            if nk and nk in h:
                return i
    return None


def import_base_csv(raw: bytes, channel: str = "base") -> dict:
    """BASE/コメフル等の注文CSV(bytes)を取り込む。CP932/UTF-8どちらにも対応。"""
    text = None
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")

    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        return {"added": 0, "skipped": 0, "error": "データ行がありません"}

    header = rows[0]
    idx = {field: _find_col(header, keys) for field, keys in _CANDIDATES.items()}
    if idx["name"] is None or idx["product"] is None:
        return {"added": 0, "skipped": 0,
                "error": "氏名または商品名の列が見つかりませんでした。列名をご確認ください。"}

    def cell(row, field):
        i = idx[field]
        return row[i].strip() if (i is not None and i < len(row)) else ""

    norm = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        norm.append({
            "external_id": cell(row, "external_id"),
            "order_date": cell(row, "order_date") or date.today().isoformat(),
            "name": cell(row, "name"),
            "kana": cell(row, "kana"),
            "zip": cell(row, "zip"),
            "address": cell(row, "address"),
            "address2": cell(row, "address2"),
            "tel": cell(row, "tel"),
            "product": cell(row, "product"),
            "qty": cell(row, "qty") or 1,
            "company": cell(row, "company"),
            "note": cell(row, "note"),
        })
    result = _save_orders(norm, channel=channel)
    result["read"] = len(norm)
    return result


# ---------------------------------------------------------------------------
# 2) API取込
# ---------------------------------------------------------------------------
def _http_post(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _http_get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def refresh_access_token(cfg: dict) -> str:
    """リフレッシュトークンからアクセストークンを取得。新しいrefresh_tokenは保存する。"""
    res = _http_post(f"{BASE_API}/oauth/token", {
        "grant_type": "refresh_token",
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "refresh_token": cfg["refresh_token"],
        "redirect_uri": cfg.get("redirect_uri", ""),
    })
    if "refresh_token" in res:
        cfg["refresh_token"] = res["refresh_token"]
        db.set_setting("base_config", cfg)
    return res["access_token"]


def fetch_orders_via_api(only_unshipped: bool = True, limit: int = 100) -> dict:
    """APIで注文を取得して取り込む。base_config 設定が必要。"""
    cfg = db.get_setting("base_config")
    if not cfg or not cfg.get("refresh_token"):
        return {"added": 0, "skipped": 0, "error": "BASE APIの認証情報が未設定です（設定ページで登録してください）"}

    token = refresh_access_token(cfg)
    params = {"limit": limit, "offset": 0, "order": "desc"}
    data = _http_get(f"{BASE_API}/orders?{urllib.parse.urlencode(params)}", token)

    norm = []
    for o in data.get("orders", []):
        items = o.get("order_items", [])
        # 発送完了API(edit_status)で使う order_item_id を保持
        item_ids = [it.get("order_item_id") or it.get("id") for it in items
                    if (it.get("order_item_id") or it.get("id"))]
        # 配送先優先、無ければ注文者
        norm.append({
            "external_id": str(o.get("unique_key") or o.get("order_id") or ""),
            "order_date": o.get("ordered", date.today().isoformat()),
            "name": o.get("delivery_name") or o.get("order_name") or "",
            "kana": o.get("delivery_kana") or "",
            "zip": o.get("delivery_zip_code") or "",
            "address": (o.get("delivery_address") or "") + (o.get("delivery_address2") or ""),
            "tel": o.get("delivery_tel") or "",
            "product": "; ".join(
                f'{it.get("item_name","")}×{it.get("amount",1)}'
                for it in items
            ) or "商品",
            "qty": sum(int(it.get("amount", 1)) for it in items) or 1,
            "note": "",
            "dispatch_ref": json.dumps(item_ids),
        })
    result = _save_orders(norm, channel="base")
    result["read"] = len(norm)
    return result


def dispatch_order(order_row) -> tuple[bool, str]:
    """BASEの1注文を発送完了(dispatched)にする。

    order_row は db.list_orders の行（dispatch_ref に order_item_id のJSON配列）。
    returns (成功, メッセージ)
    """
    cfg = db.get_setting("base_config")
    if not cfg or not cfg.get("refresh_token"):
        return False, "BASE API未設定"
    ref = order_row["dispatch_ref"] if "dispatch_ref" in order_row.keys() else ""
    try:
        item_ids = json.loads(ref) if ref else []
    except (json.JSONDecodeError, TypeError):
        item_ids = []
    if not item_ids:
        return False, "発送対象の商品IDが未取得（API取込で取得されます）"
    try:
        token = refresh_access_token(cfg)
        for iid in item_ids:
            _http_post(f"{BASE_API}/orders/edit_status", {
                "order_item_id": iid,
                "status": "dispatched",
            })
        return True, "BASE発送完了"
    except Exception as e:  # noqa: BLE001
        return False, f"BASE発送失敗: {e}"
