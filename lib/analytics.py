# -*- coding: utf-8 -*-
"""売上集計・顧客分析（RFM風の属性判定と「次の一手」提案）。

売上＝商品単価(price)×個数(qty)。注文日(order_date)が無ければ
出荷予定日(ship_date)→作成日(created_at)の順に使う。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def _today() -> date:
    return datetime.now(JST).date()


def order_date(o) -> date | None:
    """注文日を date で返す（複数の表記に対応）。不明なら None。"""
    for key in ("order_date", "ship_date", "created_at"):
        s = (o.get(key) or "").strip()
        if not s:
            continue
        m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            try:
                return date(y, mo, d)
            except ValueError:
                continue
    return None


def order_amount(o) -> float:
    """1注文の売上金額（単価×個数）。"""
    return float(o.get("price") or 0) * int(o.get("qty") or 1)


def order_kg(o) -> float:
    """1注文の精米kg（精米が必要な商品のみ）。"""
    if not o.get("needs_milling"):
        return 0.0
    return float(o.get("weight_kg") or 0) * int(o.get("qty") or 1)


# ---------------------------------------------------------------------------
# 売上ダッシュボード
# ---------------------------------------------------------------------------
def monthly_sales(orders) -> list[dict]:
    """年月ごとの売上・件数・kgを集計（新しい順）。"""
    agg: dict[str, dict] = {}
    for o in orders:
        d = order_date(o)
        if d is None:
            continue
        ym = f"{d.year:04d}-{d.month:02d}"
        a = agg.setdefault(ym, {"年月": ym, "売上": 0.0, "件数": 0, "精米kg": 0.0})
        a["売上"] += order_amount(o)
        a["件数"] += 1
        a["精米kg"] += order_kg(o)
    return [agg[k] for k in sorted(agg, reverse=True)]


def yearly_sales(orders) -> list[dict]:
    """年ごとの売上・件数を集計（新しい順）。"""
    agg: dict[int, dict] = {}
    for o in orders:
        d = order_date(o)
        if d is None:
            continue
        a = agg.setdefault(d.year, {"年": d.year, "売上": 0.0, "件数": 0})
        a["売上"] += order_amount(o)
        a["件数"] += 1
    return [agg[k] for k in sorted(agg, reverse=True)]


def product_sales(orders) -> list[dict]:
    """商品ごとの売上・個数（売上の多い順）。"""
    agg: dict[str, dict] = {}
    for o in orders:
        name = o.get("product_name") or "(不明)"
        a = agg.setdefault(name, {"商品": name, "売上": 0.0, "個数": 0})
        a["売上"] += order_amount(o)
        a["個数"] += int(o.get("qty") or 1)
    return sorted(agg.values(), key=lambda x: x["売上"], reverse=True)


# ---------------------------------------------------------------------------
# 顧客分析（属性判定＋次の一手）
# ---------------------------------------------------------------------------
# 属性: (ラベル, 色, 次の一手)
SEGMENTS = {
    "優良客": ("#C9A24B", "感謝を伝え、新米の先行案内や限定品を優先的に。電話やお礼状で関係を維持。"),
    "常連客": ("#4CAF82", "定期便・まとめ買い（送料がお得）を提案してリピートを定着させる。"),
    "新規客": ("#5B8DEF", "お礼メッセージ＋次回クーポンで“2回目の購入”を後押し。"),
    "離脱注意": ("#E0954B", "そろそろ在庫切れの頃。リマインド＋おすすめ（新米等）を一押し。"),
    "休眠客": ("#8A8FA3", "掘り起こしのDMを。新米の便りやクーポンで再来店を促す。"),
    "様子見": ("#8A8FA3", "もう少し様子を見る。次回購入があれば常連化を狙う。"),
}


def classify(recency_days: int | None, freq: int) -> str:
    """最終購入からの日数と購入回数から顧客属性を判定する。"""
    if recency_days is None:
        return "様子見"
    if recency_days > 270:
        return "休眠客"
    if recency_days > 120:
        return "離脱注意"
    # 直近（120日以内）に購入あり
    if freq >= 5:
        return "優良客"
    if freq >= 2:
        return "常連客"
    return "新規客"


def customer_stats(orders) -> list[dict]:
    """顧客ごとの購入実績と属性・次の一手を返す（売上の多い順）。"""
    today = _today()
    agg: dict[int, dict] = {}
    for o in orders:
        cid = o.get("customer_id")
        if cid is None:
            continue
        a = agg.setdefault(cid, {
            "顧客": o.get("customer_name") or "(不明)",
            "回数": 0, "累計金額": 0.0, "最終購入": None, "初回": None,
        })
        a["回数"] += 1
        a["累計金額"] += order_amount(o)
        d = order_date(o)
        if d:
            if a["最終購入"] is None or d > a["最終購入"]:
                a["最終購入"] = d
            if a["初回"] is None or d < a["初回"]:
                a["初回"] = d

    out = []
    for a in agg.values():
        last = a["最終購入"]
        recency = (today - last).days if last else None
        seg = classify(recency, a["回数"])
        out.append({
            "顧客": a["顧客"],
            "属性": seg,
            "次の一手": SEGMENTS[seg][1],
            "回数": a["回数"],
            "累計金額": round(a["累計金額"]),
            "最終購入": last.isoformat() if last else "—",
            "経過日数": recency if recency is not None else "—",
        })
    return sorted(out, key=lambda x: x["累計金額"], reverse=True)


def segment_summary(stats) -> list[dict]:
    """属性ごとの人数（SEGMENTSの並び順）。"""
    counts: dict[str, int] = {}
    for s in stats:
        counts[s["属性"]] = counts.get(s["属性"], 0) + 1
    return [{"属性": k, "人数": counts.get(k, 0),
             "色": SEGMENTS[k][0], "次の一手": SEGMENTS[k][1]}
            for k in SEGMENTS if counts.get(k, 0) > 0]
