# -*- coding: utf-8 -*-
"""設定：送り主・商品マスタ・顧客マスタ・BASE API・データ管理。"""
import pandas as pd
import streamlit as st

from lib import bootstrap, db, seed, ui

ui.setup_page("設定", icon="⚙️", subtitle="設 定")
bootstrap.ensure_initialized()

ui.section("設定")

tab_sender, tab_prod, tab_cust, tab_base, tab_data = st.tabs(
    ["📮 送り主", "🍚 商品マスタ", "👥 顧客マスタ", "🔗 BASE API", "🗃 データ管理"]
)

# ---------------------------------------------------------------------------
# 送り主
# ---------------------------------------------------------------------------
with tab_sender:
    st.caption("送り状の「ご依頼主」に入る情報です。手元の発行済データから自動設定済みですが、修正できます。")
    s = db.get_setting("sender") or {}
    with st.form("sender_form"):
        c1, c2 = st.columns(2)
        name = c1.text_input("ご依頼主名", s.get("name", ""))
        kana = c2.text_input("フリガナ", s.get("kana", ""))
        c3, c4 = st.columns([1, 3])
        zipc = c3.text_input("郵便番号", s.get("zip", ""))
        addr = c4.text_input("住所", s.get("address", ""))
        c5, c6 = st.columns(2)
        addr2 = c5.text_input("建物名等", s.get("address2", ""))
        tel = c6.text_input("電話番号", s.get("tel", ""))
        if st.form_submit_button("💾 保存", type="primary"):
            db.set_setting("sender", {
                "name": name, "kana": kana, "tel": tel,
                "zip": zipc, "address": addr, "address2": addr2,
            })
            st.success("送り主情報を保存しました。")

# ---------------------------------------------------------------------------
# 商品マスタ
# ---------------------------------------------------------------------------
with tab_prod:
    st.caption("精米量の計算に使います。「精米が必要」にチェックした商品だけが、ダッシュボードの精米量に加算されます。"
               "『品名(送り状用)』が送り状CSVに印字される文字です。")
    products = db.list_products(active_only=False)
    pdf = pd.DataFrame([{
        "id": p["id"], "商品名": p["name"], "区分": p["category"],
        "重量kg": p["weight_kg"], "精米が必要": bool(p["needs_milling"]),
        "品名(送り状用)": p["yamato_name"], "並び順": p["sort_order"],
        "有効": bool(p["active"]),
    } for p in products])

    edited = st.data_editor(
        pdf, use_container_width=True, hide_index=True, num_rows="dynamic",
        column_config={
            "id": None,
            "区分": st.column_config.SelectboxColumn("区分", options=["精米", "玄米", "複合", "その他"]),
            "精米が必要": st.column_config.CheckboxColumn("精米が必要"),
            "有効": st.column_config.CheckboxColumn("有効"),
        },
        key="prod_editor",
    )
    if st.button("💾 商品マスタを保存", type="primary"):
        for _, r in edited.iterrows():
            if not str(r["商品名"]).strip():
                continue
            db.upsert_product({
                "name": r["商品名"], "category": r["区分"] or "その他",
                "weight_kg": float(r["重量kg"] or 0),
                "needs_milling": 1 if r["精米が必要"] else 0,
                "yamato_name": r["品名(送り状用)"] or r["商品名"],
                "sort_order": int(r["並び順"] or 0),
                "active": 1 if r["有効"] else 0,
            })
        st.success("商品マスタを保存しました。")
        st.rerun()

# ---------------------------------------------------------------------------
# 顧客マスタ
# ---------------------------------------------------------------------------
with tab_cust:
    customers = db.list_customers()
    st.caption(f"登録顧客：{len(customers)} 名")
    cdf = pd.DataFrame([{
        "id": c["id"], "お届け先名": c["name"], "カナ": c["kana"],
        "郵便番号": c["zip"], "住所": c["address"], "建物": c["address2"],
        "電話": c["tel"], "会社": c["company"],
    } for c in customers])
    st.dataframe(cdf.drop(columns=["id"]), use_container_width=True, hide_index=True)
    st.caption("※ 顧客の編集・削除は今後の改良で画面から行えるようにできます。"
               "現状は注文入力ページの新規追加で登録されます。")

# ---------------------------------------------------------------------------
# BASE API
# ---------------------------------------------------------------------------
with tab_base:
    st.caption("BASE Developers で発行した認証情報を登録すると、BASE取込ページからAPI自動取得できます。")
    cfg = db.get_setting("base_config") or {}
    with st.form("base_form"):
        client_id = st.text_input("Client ID", cfg.get("client_id", ""))
        client_secret = st.text_input("Client Secret", cfg.get("client_secret", ""), type="password")
        redirect_uri = st.text_input("Redirect URI", cfg.get("redirect_uri", ""))
        refresh_token = st.text_input("リフレッシュトークン", cfg.get("refresh_token", ""), type="password")
        if st.form_submit_button("💾 保存", type="primary"):
            db.set_setting("base_config", {
                "client_id": client_id, "client_secret": client_secret,
                "redirect_uri": redirect_uri, "refresh_token": refresh_token,
            })
            st.success("BASE API設定を保存しました。")

# ---------------------------------------------------------------------------
# データ管理
# ---------------------------------------------------------------------------
with tab_data:
    st.subheader("顧客データの再取込")
    st.caption("ヤマトの発行済データCSVを追加で読み込み、顧客マスタを増やせます（過去注文は出荷済みとして記録）。")
    up = st.file_uploader("発行済データCSV", type=["csv"], key="reimport")
    imp_hist = st.checkbox("過去の注文も記録する（出荷済み扱い）", value=False)
    if up is not None and st.button("📥 取り込む"):
        result = seed.import_issued_csv(up.getvalue(), import_history=imp_hist)
        st.success(f"顧客 +{result['customers']} 名 / 注文 +{result['orders']} 件 を取り込みました。")

    st.divider()
    st.subheader("⚠️ 全データの初期化")
    st.caption("注文・顧客・設定をすべて消去します（元に戻せません）。")
    confirm = st.text_input('初期化するには「リセット」と入力してください', "")
    if st.button("🗑 全データをリセット", disabled=(confirm != "リセット")):
        db.reset_all()
        st.success("初期化しました。ページを再読み込みすると初期状態になります。")
