# -*- coding: utf-8 -*-
"""ヤマトB2クラウド 送り状CSVの読み込み・書き出し。

- 文字コードは Shift-JIS(CP932)
- 列構成は yamato_header.YAMATO_HEADER（元の発行済データCSVと同一の97列）
"""
from __future__ import annotations

import csv
import io

from .yamato_header import YAMATO_HEADER

# よく使う列のインデックス（YAMATO_HEADER の並び）
COL = {name: i for i, name in enumerate(YAMATO_HEADER)}


def read_issued_csv(path_or_bytes) -> tuple[list[str], list[list[str]]]:
    """発行済データCSV(CP932)を読み、(header, rows) を返す。"""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        text = path_or_bytes.decode("cp932", errors="replace")
    else:
        with open(path_or_bytes, encoding="cp932", errors="replace", newline="") as f:
            text = f.read()
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def col(row: list[str], header: list[str], name: str) -> str:
    """ヘッダー名で行から値を安全に取り出す。"""
    try:
        return row[header.index(name)]
    except (ValueError, IndexError):
        return ""


def build_row(order, sender: dict) -> list[str]:
    """注文1件(orders結合行) + 送り主情報 から、送り状CSVの1行(97列)を作る。"""
    row = [""] * len(YAMATO_HEADER)

    def put(name: str, value):
        if name in COL:
            row[COL[name]] = "" if value is None else str(value)

    # 送り状種類: 0=発払い, クール区分: 0=通常
    put("送り状種類", "0")
    put("クール区分", "0")

    # 出荷予定日 / お届け予定日 / 時間帯
    put("出荷予定日", order["ship_date"] or "")
    put("お届け予定（指定）日", order["delivery_date"] or "")
    put("配達時間帯", order["delivery_time"] or "")

    # お届け先（顧客）
    put("お届け先電話番号", order["tel"])
    put("お届け先郵便番号", order["zip"])
    put("お届け先住所", order["address"])
    put("お届け先住所（アパートマンション名）", order["address2"])
    put("お届け先会社・部門名１", order["company"])
    put("お届け先名", order["customer_name"])
    put("お届け先名略称カナ", order["kana"])
    put("敬称", order["honorific"] or "様")

    # ご依頼主（送り主）
    put("ご依頼主電話番号", sender.get("tel", ""))
    put("ご依頼主郵便番号", sender.get("zip", ""))
    put("ご依頼主住所", sender.get("address", ""))
    put("ご依頼主住所（アパートマンション名）", sender.get("address2", ""))
    put("ご依頼主名", sender.get("name", ""))
    put("ご依頼主略称カナ", sender.get("kana", ""))

    # 品名（数量が2個以上なら品名に個数を併記）
    name = order["yamato_name"]
    if (order["qty"] or 1) > 1:
        name = f'{name}×{order["qty"]}'
    put("品名１", name)

    # 記事
    put("記事", order["note"] or "")
    return row


def export_csv(orders: list, sender: dict) -> bytes:
    """注文リストを送り状CSV(CP932 bytes)に変換する。"""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(YAMATO_HEADER)
    for o in orders:
        writer.writerow(build_row(o, sender))
    return buf.getvalue().encode("cp932", errors="replace")
