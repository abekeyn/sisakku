# -*- coding: utf-8 -*-
"""初期データ取込：ヤマト発行済データCSV → 顧客マスタ・送り主・過去注文。"""
from __future__ import annotations

import re

from . import db, logic, yamato


def _ship_key(s: str):
    """出荷予定日を並べ替え用のタプルに（古い順ソート用）。"""
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


def import_issued_csv(path_or_bytes, import_history: bool = True) -> dict:
    """発行済データCSVを取り込む。

    - お届け先 → 顧客マスタ（**お名前で照合し、同一名は最新の住所に更新**）
    - ご依頼主 → 送り主設定（最初の1件を採用）
    - 各行 → 過去注文（status=shipped）として記録（import_history=True時）

    出荷予定日の古い順に処理するので、同じお名前の発送が複数あると
    最後（＝最新）の住所・連絡先で顧客マスタが上書きされる。

    returns {"customers": n, "orders": n, "sender": bool}
    """
    header, rows = yamato.read_issued_csv(path_or_bytes)
    if not rows:
        return {"customers": 0, "orders": 0, "sender": False}

    def g(row, name):
        return yamato.col(row, header, name).strip()

    # 古い順に処理 → 同一名は最新の住所で上書きされる
    rows = sorted(rows, key=lambda r: _ship_key(yamato.col(r, header, "出荷予定日")))

    # 既存顧客を「正規化したお名前」で索引（名前で照合するため）
    by_name = {}
    for c in db.list_customers():
        by_name.setdefault(logic.normalize_text(c["name"]), c["id"])

    sender_set = False
    n_cust = 0
    n_ord = 0

    for row in rows:
        name = g(row, "お届け先名")
        address = g(row, "お届け先住所")
        if not name and not address:
            continue

        # 送り主（最初に見つかった1件を設定として保存）
        if not sender_set and db.get_setting("sender") is None:
            sender = {
                "name": g(row, "ご依頼主名"),
                "kana": g(row, "ご依頼主略称カナ"),
                "tel": g(row, "ご依頼主電話番号"),
                "zip": g(row, "ご依頼主郵便番号"),
                "address": g(row, "ご依頼主住所"),
                "address2": g(row, "ご依頼主住所（アパートマンション名）"),
            }
            if sender["name"]:
                db.set_setting("sender", sender)
                sender_set = True

        cust = {
            "name": name,
            "kana": g(row, "お届け先名略称カナ"),
            "tel": g(row, "お届け先電話番号"),
            "zip": g(row, "お届け先郵便番号"),
            "address": address,
            "address2": g(row, "お届け先住所（アパートマンション名）"),
            "company": g(row, "お届け先会社・部門名１"),
            "honorific": g(row, "敬称") or "様",
        }
        norm = logic.normalize_text(name)
        if norm in by_name:
            cid = by_name[norm]
            # 同一名は最新の住所等で更新（空欄では既存を消さない）
            db.update_customer(cid, {k: v for k, v in cust.items() if v})
        else:
            cid = db.upsert_customer(cust)
            by_name[norm] = cid
            n_cust += 1

        if import_history:
            raw_product = g(row, "品名１")
            tracking = g(row, "伝票番号")
            # 伝票番号で重複登録を防ぐ（再取得しても増えない）
            ext = f"yamato:{tracking}" if tracking else ""
            if raw_product and not (ext and db.order_exists(ext)):
                pid = logic.match_or_create_product(raw_product)
                db.add_order({
                    "customer_id": cid,
                    "product_id": pid,
                    "qty": 1,
                    "channel": "import",
                    "order_date": g(row, "出荷予定日"),
                    "ship_date": g(row, "出荷予定日"),
                    "delivery_date": g(row, "お届け予定（指定）日"),
                    "delivery_time": g(row, "配達時間帯"),
                    "milling_kg_override": None,
                    "note": g(row, "記事"),
                    "status": "shipped",   # 過去の発行済データなので出荷済み扱い
                    "external_id": ext,
                    "tracking_no": tracking,
                })
                n_ord += 1

    return {"customers": n_cust, "orders": n_ord, "sender": db.get_setting("sender") is not None}
