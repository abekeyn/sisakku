# -*- coding: utf-8 -*-
"""トップ＝精米・発送ダッシュボード（ハブ画面）。"""
from datetime import date, datetime

import pandas as pd
import streamlit as st

from lib import base_api, bootstrap, db, exporter, komeful, logic, ui, yamato

ui.setup_page("精米・発送ダッシュボード", icon="🌾", subtitle="精 米 ・ 発 送 管 理")
bootstrap.ensure_initialized()

TIME_CODES = {
    "指定なし": "0000", "午前中": "0812", "14-16時": "1416",
    "16-18時": "1618", "18-20時": "1820", "19-21時": "1921",
}
CODE_TO_LABEL = {v: k for k, v in TIME_CODES.items()}

# ===========================================================================
# クイック操作（取込・追加）
# ===========================================================================
base_ready = bool((db.get_setting("base_config") or {}).get("refresh_token"))

a1, a2 = st.columns(2)
with a1:
    if st.button("BASE取込", use_container_width=True):
        if base_ready:
            with st.spinner("BASEから未発送の注文を取得中..."):
                r = base_api.fetch_orders_via_api()
            if r.get("error"):
                st.error(r["error"])
            else:
                st.success(f"BASE：未発送 {r.get('target', 0)} 件のうち、"
                           f"新規 {r['added']} 件を取り込みました（取込済み {r['skipped']} 件）")
        else:
            st.switch_page("pages/3_🛒_取込.py")
with a2:
    if st.button("コメフル取込", use_container_width=True):
        st.switch_page("pages/3_🛒_取込.py")

b1, b2 = st.columns(2)
with b1:
    if st.button("＋ 注文を追加", use_container_width=True):
        st.switch_page("pages/1_📝_注文入力.py")
with b2:
    if st.button("↻ 最新に更新", use_container_width=True):
        db.clear_cache()
        st.rerun()

# ===========================================================================
# 今日のサマリ（数字が必ず整合：出荷 = 精米 + 精米不要 + 要確認）
# ===========================================================================
orders = db.list_orders(status="pending")
summary = logic.milling_summary(orders)

total_qty = sum(o["qty"] or 1 for o in orders)
milling_qty = sum(p["qty"] for p in summary["by_product"])
nonmill_qty = sum(p["qty"] for p in summary["non_milling"])
check_qty = sum(x["qty"] for x in summary["needs_check"])

ui.section("今日の精米・発送")
m1, m2, m3 = st.columns(3)
m1.metric("出荷 合計", f"{total_qty} 袋", help="= 精米 + 精米不要 + 要確認")
m2.metric("精米する量", f"{summary['total_kg']:g} kg")
m3.metric("精米 袋数", f"{milling_qty} 袋")

st.caption(
    f"内訳：精米 **{milling_qty}袋**／精米不要 **{nonmill_qty}袋**"
    f"／精米量の確認待ち **{check_qty}袋**　"
    f"（合計 {total_qty}袋・{len(orders)}件）"
)

if summary["by_product"]:
    st.dataframe(
        [{"品目": p["name"], "袋数": p["qty"], "精米量(kg)": f"{p['kg']:g}"}
         for p in summary["by_product"]],
        use_container_width=True, hide_index=True,
    )
else:
    st.info("精米が必要な未出荷注文はありません。")

if summary["non_milling"]:
    st.caption("精米不要：" + "／".join(f'{p["name"]}×{p["qty"]}' for p in summary["non_milling"]))
if summary["needs_check"]:
    st.warning("精米量の確認が必要：" +
               "／".join(f'{x["customer"]}様 {x["name"]}×{x["qty"]}' for x in summary["needs_check"]))

st.markdown('<hr class="brand-rule"/>', unsafe_allow_html=True)

# ===========================================================================
# 顧客別 発送リスト
# ===========================================================================
ui.section("発送リスト", "誰に・何を・何kg")

if not orders:
    st.info("未出荷の注文はありません。")
    st.stop()

for o in orders:
    kg = (o["weight_kg"] or 0) * (o["qty"] or 1)
    kg_txt = f"（{kg:g}kg）" if o["needs_milling"] else "（精米不要）" if o["category"] in ("玄米", "その他") else ""
    st.markdown(
        f'<div class="ship-card">'
        f'<span class="ship-name">{o["customer_name"]} 様</span> '
        f'{ui.channel_badge(o["channel"])}'
        f'<div class="ship-item">{o["product_name"]} × {o["qty"]} {kg_txt}'
        f'　/　{o["zip"]} {o["address"]}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

# 操作用テーブル（選択・日付調整）
ui.section("出荷する注文を選択")
rows = [{
    "選択": True,
    "お客様": o["customer_name"],
    "内容": f'{o["product_name"]} × {o["qty"]}',
    "経路": ui.CHANNELS.get(o["channel"], {}).get("label", o["channel"]),
    "出荷予定日": o["ship_date"] or date.today().strftime("%Y/%m/%d"),
    "時間帯": CODE_TO_LABEL.get(o["delivery_time"], "指定なし"),
    "_id": o["id"],
} for o in orders]
edited = st.data_editor(
    pd.DataFrame(rows), use_container_width=True, hide_index=True,
    column_config={
        "選択": st.column_config.CheckboxColumn("選択", default=True),
        "お客様": st.column_config.TextColumn("お客様", disabled=True),
        "内容": st.column_config.TextColumn("内容", disabled=True),
        "経路": st.column_config.TextColumn("経路", disabled=True),
        "出荷予定日": st.column_config.TextColumn("出荷予定日"),
        "時間帯": st.column_config.SelectboxColumn("時間帯", options=list(TIME_CODES.keys())),
        "_id": None,
    },
    key="dash_ship_editor",
)
selected = edited[edited["選択"]]
sel_ids = [int(x) for x in selected["_id"]]
st.write(f"選択中：**{len(sel_ids)} 件**")

# ===========================================================================
# 出荷アクション
# ===========================================================================
s = db.get_setting("sender") or {}
if not s.get("name"):
    st.warning("送り主が未設定です。「設定」ページで登録してください。")

c1, c2 = st.columns(2)

with c1:
    if st.button("📄 ヤマトCSVを作成", type="primary", use_container_width=True,
                 disabled=not sel_ids):
        for _, r in edited.iterrows():
            db.update_order(int(r["_id"]), {
                "ship_date": r["出荷予定日"],
                "delivery_time": TIME_CODES.get(r["時間帯"], "0000"),
            })
        sel = set(sel_ids)
        targets = [o for o in db.list_orders(status="pending") if o["id"] in sel]
        csv_bytes = yamato.export_csv(targets, s)
        st.session_state["dash_csv"] = csv_bytes
        res = exporter.save_or_reserve(csv_bytes)
        if res["mode"] == "saved":
            st.success(f"デスクトップの『ヤマト出荷CSV』に保存しました。\n\n📄 {res['path']}")
        else:
            st.success("送り状CSVを作成しました。👇 下の「送り状CSVをダウンロード」で保存できます。\n\n"
                       "（PCを起動すると、デスクトップの『ヤマト出荷CSV』にも自動保存されます）")

with c2:
    if st.button("✓ 出荷完了（各サイトも反映）", use_container_width=True,
                 disabled=not sel_ids,
                 help="自社を出荷済みにし、BASEはAPIで自動発送完了。コメフルは管理画面リンクを表示します。"):
        sel = set(sel_ids)
        targets = [o for o in db.list_orders(status="pending") if o["id"] in sel]
        msgs, komeful_needed = [], False
        for o in targets:
            if o["channel"] == "base":
                ok, msg = base_api.dispatch_order(o)
                msgs.append(("✅" if ok else "⚠️") + f' {o["customer_name"]}様：{msg}')
            elif o["channel"] == "komeful":
                komeful_needed = True
        db.update_order_status(sel_ids, "shipped")
        st.success(f"{len(targets)} 件を出荷済みにしました。")
        for m in msgs:
            st.write(m)
        if komeful_needed:
            st.info("コメフルの注文があります。管理画面で出荷処理をしてください。")
            st.link_button("コメフル管理画面を開く", komeful.SELLER_URL, use_container_width=True)
        st.session_state["dash_reload"] = True

# CSVダウンロード（クラウドではこれが保存の主役）
if st.session_state.get("dash_csv"):
    st.download_button(
        "⬇️ 送り状CSVをダウンロード",
        data=st.session_state["dash_csv"],
        file_name=exporter.make_filename(),
        mime="text/csv", use_container_width=True, type="primary",
    )

if st.session_state.pop("dash_reload", False):
    st.rerun()
