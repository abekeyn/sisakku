# -*- coding: utf-8 -*-
"""ヤマト出力：未出荷の注文を送り状CSV(B2クラウド形式)に変換し、出荷済みにする。"""
from datetime import date, datetime

import pandas as pd
import streamlit as st

from lib import bootstrap, db, exporter, ui, yamato

ui.setup_page("ヤマト出力", icon="🚚", subtitle="ヤ マ ト 出 力")
bootstrap.ensure_initialized()

ui.section("ヤマト送り状CSVの作成")
st.caption("未出荷の注文を選んでCSVを作成 → ヤマトB2クラウドの「送り状発行 → 外部データ取込」にアップロードすると、そのまま送り状が印刷できます。")

# 配達時間帯コード（ヤマト）
TIME_CODES = {
    "指定なし": "0000", "午前中": "0812", "14-16時": "1416",
    "16-18時": "1618", "18-20時": "1820", "19-21時": "1921",
}
CODE_TO_LABEL = {v: k for k, v in TIME_CODES.items()}

sender = db.get_setting("sender") or {}
if not sender.get("name"):
    st.warning("⚠️ 送り主（ご依頼主）が未設定です。「設定」ページで登録してください。送り主が空だと送り状が正しく作れません。")

pending = db.list_orders(status="pending")
if not pending:
    st.info("未出荷の注文はありません。")
    st.stop()

# 編集用の表を作成
rows = []
for o in pending:
    rows.append({
        "選択": True,
        "お客様": o["customer_name"],
        "商品": o["product_name"],
        "数量": o["qty"],
        "出荷予定日": o["ship_date"] or date.today().strftime("%Y/%m/%d"),
        "お届け日": o["delivery_date"] or "",
        "時間帯": CODE_TO_LABEL.get(o["delivery_time"], "指定なし"),
        "_id": o["id"],
    })
df = pd.DataFrame(rows)

ui.section("出荷する注文を選択", "必要なら日付・時間帯を調整できます")
edited = st.data_editor(
    df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "選択": st.column_config.CheckboxColumn("選択", default=True),
        "お客様": st.column_config.TextColumn("お客様", disabled=True),
        "商品": st.column_config.TextColumn("商品", disabled=True),
        "数量": st.column_config.NumberColumn("数量", disabled=True),
        "出荷予定日": st.column_config.TextColumn("出荷予定日", help="YYYY/MM/DD"),
        "お届け日": st.column_config.TextColumn("お届け日", help="YYYY/MM/DD（任意）"),
        "時間帯": st.column_config.SelectboxColumn("時間帯", options=list(TIME_CODES.keys())),
        "_id": None,  # 非表示
    },
    key="ship_editor",
)

selected = edited[edited["選択"]]
st.write(f"選択中：**{len(selected)} 件**")

col1, col2 = st.columns(2)

with col1:
    if st.button("📄 ヤマトCSVを作成", type="primary", use_container_width=True,
                 disabled=selected.empty):
        # 編集内容をDBへ反映
        for _, r in edited.iterrows():
            db.update_order(int(r["_id"]), {
                "ship_date": r["出荷予定日"],
                "delivery_date": r["お届け日"],
                "delivery_time": TIME_CODES.get(r["時間帯"], "0000"),
            })
        sel_ids = set(int(x) for x in selected["_id"])
        orders_for_csv = [o for o in db.list_orders(status="pending") if o["id"] in sel_ids]
        csv_bytes = yamato.export_csv(orders_for_csv, sender)
        st.session_state["csv_bytes"] = csv_bytes
        res = exporter.save_or_reserve(csv_bytes)
        if res["mode"] == "saved":
            st.success(f"デスクトップの『ヤマト出荷CSV』に保存しました（{len(orders_for_csv)}件）。\n\n📄 {res['path']}")
        else:
            st.success(f"送り状CSVを作成しました（{len(orders_for_csv)}件）。👇 下の「送り状CSVをダウンロード」で保存できます。\n\n"
                       "（PCを起動すると、デスクトップの『ヤマト出荷CSV』にも自動保存されます）")

with col2:
    if st.button("✓ 選択した注文を「出荷済み」にする", use_container_width=True,
                 disabled=selected.empty,
                 help="CSVを保存して発送が済んだら押してください。ダッシュボードの精米量から外れます。"):
        db.update_order_status([int(x) for x in selected["_id"]], "shipped")
        st.success(f"{len(selected)} 件を出荷済みにしました。")
        st.rerun()

# ダウンロード（クラウドではこれが保存の主役）
if st.session_state.get("csv_bytes"):
    st.download_button(
        "⬇️ 送り状CSVをダウンロード",
        data=st.session_state["csv_bytes"],
        file_name=exporter.make_filename(),
        mime="text/csv",
        use_container_width=True, type="primary",
    )
    st.caption("※ Shift-JIS形式・ヤマトB2クラウド取込フォーマット。"
               "B2クラウドにアップロード → 送り状印刷 → 発送が済んだら「出荷済みにする」を押してください。")
