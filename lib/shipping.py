# -*- coding: utf-8 -*-
"""出荷確定の共通ロジック。

B2クラウドの発行済データ（伝票番号入り）と未出荷注文を照合し、
伝票番号の記録 → 出荷完了 → BASEへの発送完了＋伝票番号登録 まで行う。
アプリ（ホームのダイアログ）と常駐エージェント（自動取得）の両方から使う。
"""
from __future__ import annotations

import re

from . import base_api, db, komeful, logic, yamato


def _digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _expected_item(o) -> str:
    n, q = o["yamato_name"], o["qty"] or 1
    return f"{n}×{q}" if q > 1 else n


def match_tracking(rows: list[dict], unshipped: list[dict]):
    """発行済データの行と未出荷注文を照合する。

    rows: yamato.parse_issued_for_tracking() の結果
    returns (matches[(row, order)], unmatched_rows[])
    照合キー：電話番号（数字のみ）→ 複数候補は品名で絞る → 最後は名前で照合
    """
    remaining = list(unshipped)
    matches, unmatched = [], []
    for r in rows:
        cand = [o for o in remaining if _digits(o["tel"]) == _digits(r["tel"])]
        if len(cand) > 1:
            exact = [o for o in cand if _expected_item(o) == r["item"]]
            cand = exact or cand
        if not cand:
            cand = [o for o in remaining
                    if logic.normalize_text(o["customer_name"]) == logic.normalize_text(r["name"])]
        if cand:
            o = cand[0]
            remaining.remove(o)
            matches.append((r, o))
        else:
            unmatched.append(r)
    return matches, unmatched


def confirm_shipments(matches) -> dict:
    """照合済みの注文を出荷確定する。

    - 伝票番号を記録して出荷完了(shipped)に
    - BASEの注文は発送完了＋伝票番号をAPIで登録
    returns {"shipped": n, "messages": [...], "komeful": bool}
    """
    msgs = []
    komeful_flag = False
    for r, o in matches:
        db.update_order(o["id"], {"tracking_no": r["tracking"], "status": "shipped"})
        if o["channel"] == "base":
            o2 = dict(o)
            o2["tracking_no"] = r["tracking"]
            ok, msg = base_api.dispatch_order(o2)
            msgs.append(("✅" if ok else "⚠️") + f' {o["customer_name"]}様：{msg}')
        elif o["channel"] == "komeful":
            komeful_flag = True
    if komeful_flag:
        msgs.append(f"🛒 コメフルの注文は管理画面で出荷処理してください：{komeful.SELLER_URL}")
    return {"shipped": len(matches), "messages": msgs, "komeful": komeful_flag}


def process_issued_csv(raw: bytes) -> dict:
    """発行済データCSV（bytes）を取り込んで出荷確定まで一括実行する（エージェント用）。

    returns {"rows": n, "shipped": n, "unmatched": n, "messages": [...]}
    """
    rows = yamato.parse_issued_for_tracking(raw)
    unshipped = [o for o in db.list_orders() if o["status"] in ("pending", "milled")]
    matches, unmatched = match_tracking(rows, unshipped)
    result = confirm_shipments(matches) if matches else {"shipped": 0, "messages": [], "komeful": False}
    return {
        "rows": len(rows),
        "shipped": result["shipped"],
        "unmatched": len(unmatched),
        "messages": result["messages"],
    }
