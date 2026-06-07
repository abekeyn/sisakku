# -*- coding: utf-8 -*-
"""コメフル（komeful.co.jp）連携。

コメフルは現状、公開APIが見当たらないため自動連携はできない。
- 取込：注文CSV（出店者管理画面からダウンロードできる場合）または画面で手入力
- 出荷：出店者管理画面で手動。出荷処理ページをワンタップで開けるようにする
"""
from __future__ import annotations

from . import base_api

# 出店者（生産者）向け管理画面。出荷処理はここで行う。
SELLER_URL = "https://rb.komeful.co.jp/"


def import_komeful_csv(raw: bytes) -> dict:
    """コメフルの注文CSVを取り込む（BASEと同じ柔軟マッピングを利用）。"""
    return base_api.import_base_csv(raw, channel="komeful")


def dispatch_order(order_row) -> tuple[bool, str]:
    """コメフルはAPI自動出荷に未対応。出店者画面で手動対応してもらう。"""
    return False, f"コメフルは管理画面で出荷処理が必要です（{SELLER_URL}）"
