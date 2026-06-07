# -*- coding: utf-8 -*-
"""アプリ起動時の初期化（全ページの先頭で呼ぶ）。"""
from __future__ import annotations

from pathlib import Path

from . import db, exporter, logic, seed

ROOT = Path(__file__).resolve().parent.parent


def _find_issued_csv() -> Path | None:
    """プロジェクト直下にある『発行済データ』CSVを探す。"""
    for p in sorted(ROOT.glob("*発行済データ*.csv")):
        return p
    return None


def ensure_initialized() -> None:
    """初期化。重い処理なので「1セッション1回だけ」実行する（速度対策）。"""
    # すでにこのセッションで初期化済みなら即終了（毎操作のDB往復を避ける）
    try:
        import streamlit as st
        if st.session_state.get("_init_done"):
            return
    except Exception:  # noqa: BLE001  streamlit外（エージェント等）でも動くように
        st = None

    db.init_db()
    logic.seed_default_products()

    # 初回のみ：顧客が空なら手元の発行済データCSVから顧客マスタを自動生成
    if db.get_setting("seeded") is None:
        csv_path = _find_issued_csv()
        if csv_path is not None:
            try:
                seed.import_issued_csv(str(csv_path), import_history=True)
            except Exception as e:  # noqa: BLE001  初回取込の失敗で起動を止めない
                db.set_setting("seed_error", str(e))
        db.set_setting("seeded", True)

    # 送り主が未設定なら空のひな型を用意
    if db.get_setting("sender") is None:
        db.set_setting("sender", {
            "name": "", "kana": "", "tel": "",
            "zip": "", "address": "", "address2": "",
        })

    # 予約された出力の書き出しはPCローカルのときだけ（クラウドは常駐エージェントが担当）
    if exporter.is_local_pc():
        try:
            exporter.process_pending()
        except Exception:  # noqa: BLE001
            pass

    if st is not None:
        st.session_state["_init_done"] = True
