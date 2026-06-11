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
from datetime import date, datetime

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
            "delivery_date": o.get("delivery_date", ""),
            "delivery_time": o.get("delivery_time", ""),
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
def _http_post(url: str, data: dict, token: str | None = None) -> dict:
    body = urllib.parse.urlencode(data).encode()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
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


# BASEの希望時間帯 → ヤマトの時間帯コード（対応するものだけ）
_TIME_ZONE_MAP = {
    "am": "0812", "14_16": "1416", "16_18": "1618",
    "18_20": "1820", "19_21": "1921",
}


def fetch_orders_via_api(limit: int = 100) -> dict:
    """APIで「未発送(dispatch_status=ordered)」の注文だけを取り込む。

    - 一覧APIで全注文を取得 → 未発送だけに絞る（発送済み・キャンセルは除外）
    - 詳細API(orders/detail)で届け先住所・商品・order_item_id を取得
    - 商品(order_item)ごとに1注文として登録（精米量の集計に乗る）
    """
    cfg = db.get_setting("base_config")
    if not cfg or not cfg.get("refresh_token"):
        return {"added": 0, "skipped": 0, "error": "BASE APIの認証情報が未設定です（設定ページで登録してください）"}

    token = refresh_access_token(cfg)
    params = {"limit": limit, "offset": 0}
    data = _http_get(f"{BASE_API}/orders?{urllib.parse.urlencode(params)}", token)
    all_orders = data.get("orders", [])

    # 未発送のみ（ordered=入金済み・未対応）。発送済み/キャンセル/入金待ちは取り込まない
    targets = [o for o in all_orders if o.get("dispatch_status") == "ordered"]

    norm = []
    skipped = 0
    for o in targets:
        uk = str(o.get("unique_key") or "")
        if not uk:
            continue
        if db.order_exists_prefix(uk):  # この注文は取込済み（detail呼び出しを節約）
            skipped += 1
            continue
        detail = _http_get(f"{BASE_API}/orders/detail/{uk}", token)
        d = detail.get("order", detail)

        recv = d.get("order_receiver") or {}
        name = f'{recv.get("last_name", "")}　{recv.get("first_name", "")}'.strip("　 ")
        ts = d.get("ordered")
        order_date = (datetime.fromtimestamp(ts).strftime("%Y/%m/%d")
                      if isinstance(ts, (int, float)) else date.today().strftime("%Y/%m/%d"))
        ddate = str(d.get("delivery_date") or "").replace("-", "/")
        dtime = _TIME_ZONE_MAP.get(str(d.get("delivery_time_zone") or ""), "")

        for it in d.get("order_items", []):
            if it.get("status") and it["status"] != "ordered":
                continue  # 商品単位でも未発送のみ
            iid = it.get("order_item_id")
            norm.append({
                "external_id": f"{uk}:{iid}",
                "order_date": order_date,
                "name": name,
                "kana": "",
                "zip": recv.get("zip_code") or "",
                "address": (recv.get("prefecture") or "") + (recv.get("address") or ""),
                "address2": recv.get("address2") or "",
                "tel": recv.get("tel") or "",
                "product": it.get("title") or "商品",
                "qty": int(it.get("amount", 1) or 1),
                "note": d.get("remark") or "",
                "delivery_date": ddate,
                "delivery_time": dtime,
                "dispatch_ref": json.dumps([iid]),
            })

    result = _save_orders(norm, channel="base")
    result["read"] = len(all_orders)
    result["target"] = len(targets)
    result["skipped"] += skipped
    return result


YAMATO_DELIVERY_COMPANY_ID = 3  # BASEにおけるヤマト運輸のID


def dispatch_order(order_row) -> tuple[bool, str]:
    """BASEの1注文を発送完了(dispatched)にする。伝票番号があれば一緒に登録する。

    order_row は db.list_orders の行（dispatch_ref に order_item_id のJSON配列、
    tracking_no にヤマト伝票番号）。
    returns (成功, メッセージ)
    """
    import re as _re
    import urllib.error

    cfg = db.get_setting("base_config")
    if not cfg or not cfg.get("refresh_token"):
        return False, "BASE API未設定"
    ref = order_row.get("dispatch_ref") or ""
    try:
        item_ids = json.loads(ref) if ref else []
    except (json.JSONDecodeError, TypeError):
        item_ids = []
    if not item_ids:
        return False, "発送対象の商品IDが未取得（API取込で取得されます）"

    # 伝票番号（半角英数のみ許可のためハイフン等を除去）
    tracking = _re.sub(r"[^0-9A-Za-z]", "", str(order_row.get("tracking_no") or ""))

    try:
        token = refresh_access_token(cfg)
        for iid in item_ids:
            params = {"order_item_id": iid, "status": "dispatched"}
            if tracking:
                params["tracking_number"] = tracking
                params["delivery_company_id"] = YAMATO_DELIVERY_COMPANY_ID
            try:
                _http_post(f"{BASE_API}/orders/edit_status", params, token=token)
            except urllib.error.HTTPError:
                if not tracking:
                    raise
                # 伝票番号付きで拒否された場合は、発送完了のみ再試行
                _http_post(f"{BASE_API}/orders/edit_status", {
                    "order_item_id": iid, "status": "dispatched",
                }, token=token)
                tracking = ""  # メッセージ用
        msg = "BASE発送完了" + (f"（伝票番号 {tracking} を登録）" if tracking else "")
        return True, msg
    except Exception as e:  # noqa: BLE001
        return False, f"BASE発送失敗: {e}"
