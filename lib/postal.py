# -*- coding: utf-8 -*-
"""郵便番号 → 住所（都道府県・市区町村・町域）検索。

zipcloud（日本郵便のデータを使った無料API・認証不要）を使う。
https://zipcloud.ibsnet.co.jp/doc/api
"""
from __future__ import annotations

import json
import re
import urllib.request

ZIPCLOUD_API = "https://zipcloud.ibsnet.co.jp/api/search"


def lookup_address(zip_code: str) -> str | None:
    """7桁の郵便番号から住所（都道府県+市区町村+町域）を返す。見つからなければNone。"""
    digits = re.sub(r"\D", "", str(zip_code or ""))
    if len(digits) != 7:
        return None
    try:
        url = f"{ZIPCLOUD_API}?zipcode={digits}"
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return None
    results = data.get("results")
    if not results:
        return None
    a = results[0]
    return f'{a.get("address1", "")}{a.get("address2", "")}{a.get("address3", "")}'
