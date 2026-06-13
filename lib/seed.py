# -*- coding: utf-8 -*-
"""初期データ取込：ヤマト発行済データCSV → 顧客マスタ・送り主・過去注文。"""
from __future__ import annotations

from datetime import date

from . import db, logic, yamato


def import_issued_csv(path_or_bytes, import_history: bool = True) -> dict:
    """発行済データCSVを取り込む。

    - お届け先 → 顧客マスタ（name+address で重複排除）
    - ご依頼主 → 送り主設定（最初の1件を採用）
    - 各行 → 過去注文（status=shipped）として記録（import_history=True時）

    returns {"customers": n, "orders": n, "sender": bool}
    """
    header, rows = yamato.read_issued_csv(path_or_bytes)
    if not rows:
        return {"customers": 0, "orders": 0, "sender": False}

    def g(row, name):
        return yamato.col(row, header, name).strip()

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
        existed = db.find_customer(name, address) is not None
        cid = db.upsert_customer(cust)
        if not existed:
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
