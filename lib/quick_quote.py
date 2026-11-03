# -*- coding: utf-8 -*-
"""かんたん見積：精米・玄米の数量と届け先の都道府県から、米代＋送料の総額を出す。

送料は、ヤマト運輸との契約運賃表（宅急便・税抜・発地＝南東北〔福島〕）をそのまま持つ。
契約が変わったら RATES を差し替える。

箱の分け方：精米と玄米は同じ箱に入れられるので、合計の重さ（kg）を箱に分ける。
1箱は30kgまで（180サイズ）。運賃の合計がいちばん安くなる分け方を探す
（例：関東へ35kg → 30kg+5kgより、20kg+15kgのほうが安い）。
"""
from __future__ import annotations

import math

from . import db

PRICES_KEY = "quick_quote_prices"   # {"seimai": 円/5kg(税込), "genmai": 円/5kg(税込)}
DEFAULT_PRICES = {"seimai": 3500.0, "genmai": 3150.0}   # 米代（税込・送料別）。5kgあたり
RICE_TAX = 8                        # お米は軽減税率
SHIP_TAX = 10                       # 送料は標準税率
MAX_BOX_KG = 30

AREAS = ["北海道", "北東北", "南東北", "関東", "信越", "北陸", "中部", "関西",
         "中国", "四国", "九州", "沖縄"]

PREF_AREA = {
    "北海道": "北海道",
    **{p: "北東北" for p in ("青森県", "岩手県", "秋田県")},
    **{p: "南東北" for p in ("宮城県", "山形県", "福島県")},
    **{p: "関東" for p in ("茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都",
                          "神奈川県", "山梨県")},
    **{p: "信越" for p in ("新潟県", "長野県")},
    **{p: "北陸" for p in ("富山県", "石川県", "福井県")},
    **{p: "中部" for p in ("岐阜県", "静岡県", "愛知県", "三重県")},
    **{p: "関西" for p in ("滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県")},
    **{p: "中国" for p in ("鳥取県", "島根県", "岡山県", "広島県", "山口県")},
    **{p: "四国" for p in ("徳島県", "香川県", "愛媛県", "高知県")},
    **{p: "九州" for p in ("福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県")},
    "沖縄県": "沖縄",
}
PREFECTURES = list(PREF_AREA)       # 北から順（辞書の挿入順）

# サイズ → (重量の上限kg, 各エリアの運賃〔AREASの順・税抜〕)
RATES = {
    60:  (2,  [850, 500, 500, 500, 500, 610, 610, 730, 970, 970, 1250, 1250]),
    80:  (5,  [950, 600, 600, 600, 600, 710, 710, 830, 1070, 1070, 1350, 1650]),
    100: (10, [1050, 700, 700, 700, 700, 810, 810, 930, 1170, 1170, 1450, 2050]),
    120: (15, [1150, 800, 800, 800, 800, 910, 910, 1030, 1270, 1270, 1550, 2450]),
    140: (20, [1350, 1000, 1000, 1000, 1000, 1110, 1110, 1230, 1470, 1470, 1750, 2950]),
    160: (25, [1550, 1200, 1200, 1200, 1200, 1310, 1310, 1430, 1670, 1670, 1950, 3450]),
    180: (30, [2450, 1400, 1400, 1400, 1400, 2010, 2010, 2330, 2570, 2570, 3650, 6150]),
}


def get_prices() -> dict:
    saved = db.get_setting(PRICES_KEY) or {}
    return {k: float(saved.get(k, v)) for k, v in DEFAULT_PRICES.items()}


def save_prices(seimai: float, genmai: float) -> None:
    db.set_setting(PRICES_KEY, {"seimai": float(seimai), "genmai": float(genmai)})


def box_size(kg: int) -> int:
    """重さ（kg）が入る最小のサイズ。"""
    for size, (limit, _) in RATES.items():
        if kg <= limit:
            return size
    raise ValueError(f"{kg}kgは1箱に入りません")


def box_rate(kg: int, area: str) -> int:
    """1箱（kg）の運賃（税抜）。"""
    return RATES[box_size(kg)][1][AREAS.index(area)]


def plan_boxes(total_kg: float, area: str) -> list[dict]:
    """合計kgを箱に分け、運賃がいちばん安い分け方を返す。

    運賃が同じなら、箱の数が少ないほうを選ぶ。返り値は重い箱から順の
    [{"kg", "size", "fare"}]（fare＝税抜）。
    """
    w = math.ceil(total_kg - 1e-9)
    if w <= 0:
        return []
    inf = (10 ** 12, 10 ** 6)
    best = [(0, 0)] + [inf] * w            # (運賃合計, 箱数)
    pick = [0] * (w + 1)
    for n in range(1, w + 1):
        for b in range(1, min(MAX_BOX_KG, n) + 1):
            c, k = best[n - b]
            cand = (c + box_rate(b, area), k + 1)
            if cand < best[n]:
                best[n], pick[n] = cand, b
    boxes, n = [], w
    while n > 0:
        boxes.append(pick[n])
        n -= pick[n]
    return [{"kg": b, "size": box_size(b), "fare": box_rate(b, area)}
            for b in sorted(boxes, reverse=True)]


def estimate(seimai_kg: float, genmai_kg: float, pref: str, prices: dict | None = None) -> dict:
    """見積の総額。金額はすべて円。米代は税込の単価（5kgあたり）×kg÷5、送料は運賃（税抜）に10%。"""
    prices = prices or get_prices()
    area = PREF_AREA[pref]
    rice_seimai = round(prices["seimai"] / 5 * max(seimai_kg, 0))
    rice_genmai = round(prices["genmai"] / 5 * max(genmai_kg, 0))
    boxes = plan_boxes(max(seimai_kg, 0) + max(genmai_kg, 0), area)
    ship_excl = sum(b["fare"] for b in boxes)
    ship_incl = round(ship_excl * (100 + SHIP_TAX) / 100)
    rice = rice_seimai + rice_genmai
    return {"pref": pref, "area": area, "boxes": boxes,
            "seimai": rice_seimai, "genmai": rice_genmai, "rice": rice,
            "ship_excl": ship_excl, "ship": ship_incl, "total": rice + ship_incl}


def to_quote_items(est: dict, seimai_kg: float, genmai_kg: float, prices: dict) -> list[dict]:
    """見積書の明細行（税込単価）にする。lib/quote.py の clean_items が受け取る形。"""
    items = []
    if seimai_kg > 0:
        items.append({"name": "令和8年度　福島県産 コシヒカリ（精米）", "cond": "", "qty": seimai_kg,
                      "unit": "kg", "price": prices["seimai"] / 5, "rate": RICE_TAX})
    if genmai_kg > 0:
        items.append({"name": "令和8年度　福島県産 コシヒカリ（玄米）", "cond": "", "qty": genmai_kg,
                      "unit": "kg", "price": prices["genmai"] / 5, "rate": RICE_TAX})
    if est["ship"] > 0:
        items.append({"name": f"送料（{est['pref']}・{len(est['boxes'])}箱）", "cond": "",
                      "qty": 1.0, "unit": "式", "price": est["ship"], "rate": SHIP_TAX})
    return items
