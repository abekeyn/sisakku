# -*- coding: utf-8 -*-
"""取込：BASE（CSV/API）とコメフル（CSV）の注文を取り込む。"""
import streamlit as st

from lib import base_api, bootstrap, db, komeful, ui

ui.setup_page("注文取込", icon="🛒", subtitle="注 文 取 込")
bootstrap.ensure_initialized()

ui.section("注文を取り込む")

tab_base, tab_komeful = st.tabs(["🟢 BASE", "🟡 コメフル"])

# ---------------------------------------------------------------- BASE
with tab_base:
    cfg = db.get_setting("base_config") or {}
    if cfg.get("refresh_token"):
        if st.button("🔄 BASEから自動取得（API）", type="primary"):
            with st.spinner("未発送の注文を取得中..."):
                r = base_api.fetch_orders_via_api()
            if r.get("error"):
                st.error(r["error"])
            else:
                st.success(f"未発送 {r.get('target', 0)} 件のうち、新規 {r['added']} 件を取り込みました"
                           f"（取込済み {r['skipped']} 件／発送済み・キャンセルは除外）")
        st.divider()
    else:
        st.info("API自動取得を使うには「設定」ページでBASEの認証情報を登録してください。下のCSV取込は今すぐ使えます。")

    st.caption("BASE管理画面 → 注文管理 → CSVダウンロード で出力した注文CSVをアップロード。")
    up = st.file_uploader("BASE 注文CSV", type=["csv"], key="base_csv")
    if up is not None and st.button("📥 取り込む（BASE CSV）"):
        r = base_api.import_base_csv(up.getvalue())
        if r.get("error"):
            st.error(r["error"])
        else:
            st.success(f"読込 {r.get('read',0)} 件／追加 {r['added']} 件／既存 {r['skipped']} 件")

# ---------------------------------------------------------------- コメフル
with tab_komeful:
    st.caption("コメフルは現在、公開APIが無いためCSV取込または手入力です。"
               f"出店者管理画面（{komeful.SELLER_URL}）から注文CSVを出力できる場合はここで取り込めます。")
    st.link_button("🛒 コメフル管理画面を開く", komeful.SELLER_URL)
    up2 = st.file_uploader("コメフル 注文CSV", type=["csv"], key="komeful_csv")
    if up2 is not None and st.button("📥 取り込む（コメフル CSV）"):
        r = komeful.import_komeful_csv(up2.getvalue())
        if r.get("error"):
            st.error(r["error"])
        else:
            st.success(f"読込 {r.get('read',0)} 件／追加 {r['added']} 件／既存 {r['skipped']} 件")
    st.caption("※ CSVが無い注文（メール等）は「注文入力」ページで経路『コメフル』を選んで追加してください。")
