# -*- coding: utf-8 -*-
"""注文入力：LINE/コメフル等の注文を、既存顧客を選ぶだけで追加。新規はフォーム入力。"""
from datetime import date

import streamlit as st

from lib import bootstrap, db, ui

ui.setup_page("注文入力", icon="📝", subtitle="注 文 入 力")
bootstrap.ensure_initialized()

ui.section("注文を追加")
st.caption("LINEやコメフルなどで受けた注文をここで追加します。既存のお客様は選ぶだけ、新規のときだけ住所などを入力します。")

CHANNEL_OPTS = {"LINE": "line", "コメフル": "komeful", "手入力": "manual"}

customers = db.list_customers()
products = db.list_products()
cust_labels = {f'{c["name"]}（{c["address"]}）': c["id"] for c in customers}
prod_labels = {p["name"]: p["id"] for p in products}

tab_existing, tab_new = st.tabs(["👤 既存のお客様", "🆕 新規のお客様"])

with tab_existing:
    if not customers:
        st.info("まだ顧客が登録されていません。「新規のお客様」タブから追加してください。")
    else:
        with st.form("order_existing", clear_on_submit=True):
            sel = st.selectbox("お客様を選択（名前で検索できます）", list(cust_labels.keys()))
            c1, c2 = st.columns(2)
            prod = c1.selectbox("商品", list(prod_labels.keys()))
            qty = c2.number_input("数量", min_value=1, value=1, step=1)
            c3, c4 = st.columns(2)
            ch = c3.selectbox("注文の経路", list(CHANNEL_OPTS.keys()))
            mill = c4.number_input("複合の精米kg（複合のみ・1個あたり）", min_value=0.0, value=0.0, step=1.0)
            c5, c6 = st.columns(2)
            ship = c5.date_input("出荷予定日", value=date.today())
            note = c6.text_input("記事・メモ（任意）", "")
            if st.form_submit_button("➕ この注文を追加", use_container_width=True, type="primary"):
                db.add_order({
                    "customer_id": cust_labels[sel], "product_id": prod_labels[prod],
                    "qty": int(qty), "channel": CHANNEL_OPTS[ch],
                    "order_date": date.today().isoformat(),
                    "ship_date": ship.strftime("%Y/%m/%d"),
                    "delivery_date": "", "delivery_time": "",
                    "milling_kg_override": float(mill) if mill > 0 else None,
                    "note": note, "status": "pending", "external_id": "",
                })
                st.success(f"「{sel}」の注文を追加しました。")

with tab_new:
    with st.form("order_new", clear_on_submit=True):
        st.markdown("**お客様情報（新規）**")
        n1, n2 = st.columns(2)
        name = n1.text_input("お届け先名 *", "")
        kana = n2.text_input("フリガナ（任意）", "")
        n3, n4 = st.columns([1, 3])
        zipc = n3.text_input("郵便番号 *", "", placeholder="9630211")
        addr = n4.text_input("住所 *", "")
        n5, n6 = st.columns(2)
        addr2 = n5.text_input("建物名・部屋番号（任意）", "")
        tel = n6.text_input("電話番号 *", "")
        company = st.text_input("会社・部門名（任意）", "")

        st.markdown("**注文内容**")
        o1, o2 = st.columns(2)
        prod_n = o1.selectbox("商品", list(prod_labels.keys()), key="newprod")
        qty_n = o2.number_input("数量", min_value=1, value=1, step=1, key="newqty")
        o3, o4 = st.columns(2)
        ch_n = o3.selectbox("注文の経路", list(CHANNEL_OPTS.keys()), key="newch")
        mill_n = o4.number_input("複合の精米kg（複合のみ）", min_value=0.0, value=0.0, step=1.0, key="newmill")
        s1, s2 = st.columns(2)
        ship_n = s1.date_input("出荷予定日", value=date.today(), key="newship")
        note_n = s2.text_input("記事・メモ（任意）", "", key="newnote")

        if st.form_submit_button("➕ 新規お客様＋注文を追加", use_container_width=True, type="primary"):
            if not (name and zipc and addr and tel):
                st.error("お届け先名・郵便番号・住所・電話番号は必須です。")
            else:
                cid = db.upsert_customer({
                    "name": name, "kana": kana, "tel": tel, "zip": zipc,
                    "address": addr, "address2": addr2, "company": company, "honorific": "様",
                })
                db.add_order({
                    "customer_id": cid, "product_id": prod_labels[prod_n],
                    "qty": int(qty_n), "channel": CHANNEL_OPTS[ch_n],
                    "order_date": date.today().isoformat(),
                    "ship_date": ship_n.strftime("%Y/%m/%d"),
                    "delivery_date": "", "delivery_time": "",
                    "milling_kg_override": float(mill_n) if mill_n > 0 else None,
                    "note": note_n, "status": "pending", "external_id": "",
                })
                st.success(f"新規お客様「{name}」と注文を追加しました。")

st.markdown('<hr class="brand-rule"/>', unsafe_allow_html=True)
ui.section("未出荷の注文")
pending = db.list_orders(status="pending")
if not pending:
    st.caption("未出荷の注文はありません。")
else:
    for o in pending:
        cols = st.columns([4, 3, 1, 2, 1])
        cols[0].write(f'**{o["customer_name"]}** 様')
        cols[1].write(o["product_name"])
        cols[2].write(f'×{o["qty"]}')
        cols[3].markdown(ui.channel_badge(o["channel"]), unsafe_allow_html=True)
        if cols[4].button("🗑", key=f"del{o['id']}", help="削除"):
            db.delete_order(o["id"])
            st.rerun()
