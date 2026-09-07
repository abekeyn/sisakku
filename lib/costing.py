# -*- coding: utf-8 -*-
"""生産原価・価格くらべ・年間収支の計算。

三つを一本の数字でつなぐ：

1. 生産原価 : 年間の費目合計 ÷ 年間玄米生産量 ＝ 玄米1kgあたりの原価
2. 価格くらべ: 30kg いくらで出すかに対し、農協の概算金（玄米）と手取りを比べる
3. 年間収支 : 年度の実績売上 − 売上原価 − 精米/資材送料 ＝ 差引利益

単位の約束（ここを間違えると全部ずれる）：
- 農協の概算金は **玄米** 60kg あたりの価格
- 数量は **納品する量**。白米で売るなら、必要な玄米は歩留まりから逆算する
- 資材・送料は 30kg 1口あたり、精米コストは 精米1kgあたり
"""
from __future__ import annotations

from datetime import date

from lib import analytics, db, ledger

SETTINGS_KEY = "costing"

# 生産費の費目（農水省「米生産費調査」の区分にならった既定の並び）
DEFAULT_COST_ITEMS = [
    "種苗費", "肥料費", "農薬費", "光熱動力費", "その他諸材料費",
    "土地改良・水利費", "賃借料・作業委託費", "農機具費（減価償却）",
    "建物・施設費（減価償却）", "修繕費", "労働費（雇用）", "地代（借地料）",
    "共済・保険料", "その他",
]

DEFAULTS = {
    # --- 価格くらべの既定値 ---
    "form": "白米",              # 白米 / 玄米
    "price_30kg": 16000.0,       # 提示した価格（30kgあたり）
    "yield_pct": 92.0,           # 精米歩留まり（%）
    "milling_per_kg": 0.0,       # 精米コスト（円/kg・精米）
    "material_per_30kg": 0.0,    # 資材・送料（円/30kg 1口）
    "qty_t": 10.0,               # 納品する量（t）
    "ja_per_60kg": 18000.0,      # 農協の概算金（円/玄米60kg）
    # --- 生産原価の既定値 ---
    "area_a": 0.0,               # 作付面積（a）
    "yield_per_10a": 0.0,        # 単収（玄米kg / 10a）
    "genmai_kg": 0.0,            # 年間玄米生産量の直接入力（0なら面積×単収）
    "cost_items": [],            # [{"費目": str, "年額": float}, ...]
}


# ---------------------------------------------------------------------------
# 設定（既定値）
# ---------------------------------------------------------------------------
def load() -> dict:
    """保存済みの既定値。未設定のキーは DEFAULTS で埋める。"""
    saved = db.get_setting(SETTINGS_KEY) or {}
    s = dict(DEFAULTS)
    for k, v in saved.items():
        if k in DEFAULTS:
            s[k] = v
    if not s["cost_items"]:
        s["cost_items"] = [{"費目": n, "年額": 0.0} for n in DEFAULT_COST_ITEMS]
    return s


def save(s: dict) -> None:
    db.set_setting(SETTINGS_KEY, {k: s.get(k, DEFAULTS[k]) for k in DEFAULTS})


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 価格くらべ
# ---------------------------------------------------------------------------
def quote(price_30kg: float, s: dict, *, qty_kg: float | None = None,
          haku: bool | None = None) -> dict:
    """30kg いくらで出すかを、農協の概算金と同じ土俵（玄米ベース）で比べる。

    qty_kg は **納品する量**（白米モードなら白米のkg）。
    """
    haku = (s.get("form") == "白米") if haku is None else haku
    y = _f(s.get("yield_pct"), 92.0) / 100 if haku else 1.0
    ship = _f(s.get("qty_t")) * 1000 if qty_kg is None else _f(qty_kg)
    unit = _f(price_30kg) / 30                      # 納品物1kgの単価
    brown = ship / y if y else 0.0                  # それに必要な玄米
    lots = ship / 30                                # 30kg 何口ぶんか
    rev = ship * unit
    c_mill = ship * _f(s.get("milling_per_kg")) if haku else 0.0
    c_mat = lots * _f(s.get("material_per_30kg"))
    net = rev - c_mill - c_mat
    ja_per_kg = _f(s.get("ja_per_60kg")) / 60
    ja_rev = brown * ja_per_kg
    net_per_brown = net / brown if brown else 0.0
    return {
        "白米": haku,
        "単価": unit,                    # 円/kg（納品物）
        "5kg": unit * 5,                 # 円/5kg（提示額 ÷ 6）
        "納品量kg": ship,
        "必要玄米kg": brown,
        "口数": lots,
        "売上": rev,
        "精米コスト": c_mill,
        "資材送料": c_mat,
        "手取り": net,
        "玄米換算単価": net_per_brown,   # 円/kg（玄米）
        "農協単価": ja_per_kg,           # 円/kg（玄米）
        "農協収入": ja_rev,
        "差額": net - ja_rev,
        "差率": (net - ja_rev) / ja_rev * 100 if ja_rev else 0.0,
        "kg差": net_per_brown - ja_per_kg,
    }


def quote_table(s: dict, lo: int = 12000, hi: int = 30000,
                step: int = 500) -> list[dict]:
    """早見表。30kgの提示額を刻んで、いまの条件で比べた行を返す。"""
    rows = []
    for p in range(lo, hi + 1, step):
        r = quote(p, s)
        rows.append({
            "30kg提示額": p,
            "5kg換算": r["5kg"],
            "単価": r["単価"],
            "玄米換算": r["玄米換算単価"],
            "農協比": r["差率"],
            "差/kg": r["kg差"],
            "差額": r["差額"],
        })
    return rows


# ---------------------------------------------------------------------------
# 生産原価
# ---------------------------------------------------------------------------
def production(s: dict) -> dict:
    """年間の生産原価と、玄米1kgあたりの原価。"""
    items = [{"費目": i.get("費目") or "", "年額": _f(i.get("年額"))}
             for i in (s.get("cost_items") or [])]
    total = sum(i["年額"] for i in items)
    direct = _f(s.get("genmai_kg"))
    area = _f(s.get("area_a"))
    per10a = _f(s.get("yield_per_10a"))
    kg = direct if direct > 0 else area / 10 * per10a
    return {
        "費目": items,
        "年間原価": total,
        "玄米生産量kg": kg,
        "原価kg": total / kg if kg else 0.0,
        "面積由来": direct <= 0,
        "面積a": area,
        "単収": per10a,
        "反数": area / 10,
    }


# ---------------------------------------------------------------------------
# 年間収支
# ---------------------------------------------------------------------------
def sold_volume(orders, start: date, end: date, flags: dict,
                yield_pct: float) -> dict:
    """年度に販売した量。精米は歩留まりで割り戻して玄米ベースにそろえる。"""
    y = _f(yield_pct, 92.0) / 100 or 0.92
    rows = ledger.sales_detail(orders, start, end, flags)
    genmai = sum(r["玄米kg"] for r in rows)
    seimai = sum(r["精米kg"] for r in rows)
    return {
        "件数": len(rows),
        "玄米kg": genmai,
        "精米kg": seimai,
        # 精米◯kgを作るのに要った玄米まで戻す（原価はここに乗る）
        "玄米換算kg": genmai + (seimai / y if y else 0.0),
        "口数": (genmai + seimai) / 30,
    }


def annual(orders, start: date, end: date, flags: dict, s: dict) -> dict:
    """年度の収支。売上は実績、原価は生産原価から按分した概算。"""
    prod = production(s)
    in_range = [o for o in orders
                if (d := analytics.order_date(o)) and start <= d <= end]
    sales = sum(analytics.order_amount(o) for o in in_range)
    vol = sold_volume(orders, start, end, flags, s.get("yield_pct"))

    cogs = vol["玄米換算kg"] * prod["原価kg"]
    c_mill = vol["精米kg"] * _f(s.get("milling_per_kg"))
    c_mat = vol["口数"] * _f(s.get("material_per_30kg"))
    profit = sales - cogs - c_mill - c_mat

    ja_per_kg = _f(s.get("ja_per_60kg")) / 60
    ja_rev = vol["玄米換算kg"] * ja_per_kg       # 同じ量を農協に出したら
    ja_all = prod["玄米生産量kg"] * ja_per_kg    # 全量を農協に出したら

    # 手取り（売上から精米・資材送料を引いた額）の玄米1kgあたり
    net = sales - c_mill - c_mat
    net_per_kg = net / vol["玄米換算kg"] if vol["玄米換算kg"] else 0.0
    return {
        "生産": prod,
        "件数": len(in_range),
        "売上": sales,
        "販売量": vol,
        "売上原価": cogs,
        "精米コスト": c_mill,
        "資材送料": c_mat,
        "差引利益": profit,
        "手取り単価": net_per_kg,
        "農協単価": ja_per_kg,
        "農協収入": ja_rev,
        "直販上乗せ": net - ja_rev,
        "農協全量": ja_all,
        "在庫kg": prod["玄米生産量kg"] - vol["玄米換算kg"],
        # 年間原価をまかなうのに必要な販売量（いまの手取り単価で）
        "損益分岐kg": prod["年間原価"] / net_per_kg if net_per_kg > 0 else 0.0,
    }


def monthly(orders, start: date, end: date) -> list[dict]:
    """年度内の月別売上。グラフ用に年月の昇順で返す。"""
    buckets: dict[str, dict] = {}
    for o in orders:
        d = analytics.order_date(o)
        if not d or not (start <= d <= end):
            continue
        k = f"{d.year}-{d.month:02d}"
        b = buckets.setdefault(k, {"年月": k, "売上": 0.0, "件数": 0})
        b["売上"] += analytics.order_amount(o)
        b["件数"] += 1
    return sorted(buckets.values(), key=lambda x: x["年月"])
