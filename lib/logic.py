# -*- coding: utf-8 -*-
"""業務ロジック：品名の正規化・精米量の集計・初期マスタ。"""
from __future__ import annotations

import re
import unicodedata

from . import db

# 初期商品マスタ（取込CSVに出てきた品目をもとに定義）
# weight_kg は精米量計算に使う重量。needs_milling=1 のものだけ精米対象。
DEFAULT_PRODUCTS = [
    # name,        category, weight_kg, needs_milling, yamato_name, sort_order
    ("精米5kg",   "精米", 5,  1, "精米５㎏",  10),
    ("精米10kg",  "精米", 10, 1, "精米１０㎏", 20),
    ("精米15kg",  "精米", 15, 1, "精米１５㎏", 30),
    ("精米20kg",  "精米", 20, 1, "精米２０㎏", 40),
    ("精米30kg",  "精米", 30, 1, "精米３０㎏", 50),
    ("玄米5kg",   "玄米", 5,  0, "玄米５㎏",  60),
    ("玄米10kg",  "玄米", 10, 0, "玄米１０㎏", 70),
    ("玄米30kg",  "玄米", 30, 0, "玄米３０㎏", 80),
    ("複合20kg",  "複合", 20, 0, "複合２０㎏", 90),
    ("複合30kg",  "複合", 30, 0, "複合３０㎏", 100),
    ("やさい",    "その他", 0, 0, "やさい",   110),
]


def normalize_text(s: str) -> str:
    """全角→半角・空白除去・小文字化して比較しやすくする。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)  # ５→5, ㎏→kg, 全角空白→半角 等
    s = s.replace(" ", "").replace("　", "").lower()
    return s


def parse_product_name(raw: str) -> dict:
    """『精米５㎏』『精米5㎏』等の生の品名を解析して
    category / weight_kg / needs_milling を推定する。"""
    n = normalize_text(raw)  # 例: 精米5kg
    category = "その他"
    needs_milling = 0
    if "精米" in n:
        category, needs_milling = "精米", 1
    elif "玄米" in n:
        category, needs_milling = "玄米", 0
    elif "複合" in n:
        category, needs_milling = "複合", 0
    elif "やさい" in n or "野菜" in n:
        category, needs_milling = "その他", 0

    m = re.search(r"(\d+(?:\.\d+)?)\s*kg", n)
    weight = float(m.group(1)) if m else 0.0
    return {"category": category, "weight_kg": weight, "needs_milling": needs_milling}


def seed_default_products() -> None:
    """初期商品マスタを登録（既存があれば触らない）。"""
    existing = {p["name"] for p in db.list_products(active_only=False)}
    for name, cat, w, mill, yname, order in DEFAULT_PRODUCTS:
        if name in existing:
            continue
        db.upsert_product({
            "name": name, "category": cat, "weight_kg": w,
            "needs_milling": mill, "yamato_name": yname,
            "sort_order": order, "active": 1,
        })


def match_or_create_product(raw_name: str) -> int:
    """生の品名から既存商品を探し、無ければ自動で商品マスタに追加。product_id を返す。"""
    target = normalize_text(raw_name)
    for p in db.list_products(active_only=False):
        if normalize_text(p["name"]) == target or normalize_text(p["yamato_name"]) == target:
            return p["id"]
    # 未知の品名 → 推定して新規登録
    info = parse_product_name(raw_name)
    label = unicodedata.normalize("NFKC", raw_name).strip()
    return db.upsert_product({
        "name": label, "category": info["category"],
        "weight_kg": info["weight_kg"], "needs_milling": info["needs_milling"],
        "yamato_name": raw_name.strip(), "sort_order": 999, "active": 1,
    })


def milling_summary(orders: list) -> dict:
    """未出荷注文リストから精米サマリを作る。

    returns {
        "total_kg": float,            # 精米すべき合計kg
        "by_product": [ {name, qty, kg}... ],   # 精米対象の内訳
        "non_milling": [ {name, qty} ],         # 精米不要（玄米・やさい等）
        "needs_check": [ {customer, name, qty} ],  # 複合など要確認
    }
    """
    by_product: dict[str, dict] = {}
    non_milling: dict[str, int] = {}
    needs_check: list[dict] = []
    total = 0.0

    for o in orders:
        qty = o["qty"] or 1
        if o["category"] == "複合":
            # 1個あたり精米kgが入力されていれば集計、無ければ要確認
            if o["milling_kg_override"]:
                kg = o["milling_kg_override"] * qty
                key = f'{o["product_name"]}（精米{o["milling_kg_override"]:g}kg分）'
                d = by_product.setdefault(key, {"name": key, "qty": 0, "kg": 0.0})
                d["qty"] += qty
                d["kg"] += kg
                total += kg
            else:
                needs_check.append({
                    "customer": o["customer_name"],
                    "name": o["product_name"],
                    "qty": qty,
                })
        elif o["needs_milling"]:
            kg = (o["weight_kg"] or 0) * qty
            d = by_product.setdefault(o["product_name"],
                                      {"name": o["product_name"], "qty": 0, "kg": 0.0})
            d["qty"] += qty
            d["kg"] += kg
            total += kg
        else:
            non_milling[o["product_name"]] = non_milling.get(o["product_name"], 0) + qty

    return {
        "total_kg": total,
        "by_product": sorted(by_product.values(), key=lambda x: -x["kg"]),
        "non_milling": [{"name": k, "qty": v} for k, v in sorted(non_milling.items())],
        "needs_check": needs_check,
    }
