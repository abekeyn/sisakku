# -*- coding: utf-8 -*-
"""グラナダ様 請求書の承認・送信ページ（スマホで完結）。

月末にクラウド(GitHub Actions)が作成した「承認待ち」請求書を確認し、
『送信する』を押すと keiri@granada-jp.net へ送信する。PC不要。
通知のリンク（/invoice_approve）から開く想定。
"""
import base64

import streamlit as st

from lib import bootstrap, db, ui
from lib import granada_cloud as gc

ui.setup_page()
bootstrap.ensure_initialized()
ui.require_login()

st.title("📨 グラナダ様 請求書の承認・送信")

p = gc.get_pending()

if not p:
    st.info("承認待ちの請求書はありません。月末（毎月末日15時頃）に自動で作成されます。")
    st.stop()

m = p["month"]
status = p.get("status", "pending")

if status == "sent":
    st.success(f"✅ {m}月分は送信済みです（{p.get('sent_at','')}）。")
    st.caption(f"宛先 {gc.CUSTOMER_EMAIL} ／ 書類番号 {p['doc_number']} ／ ¥{p['amount']:,}")
    st.stop()

# ---- 承認待ちの内容を表示 ----
st.subheader(f"{m}月分 請求書（承認待ち）")
c1, c2, c3 = st.columns(3)
c1.metric("ご請求金額（税込）", f"¥{p['amount']:,}")
c2.metric("数量", f"{p['qty']:g} 個")
c3.metric("対象出荷", f"{len(p['rows'])} 件 / {p['total_kg']:g}kg")
st.caption(f"宛先 {gc.CUSTOMER_EMAIL} ／ 発行日 {p['issue_date']} ／ 書類番号 {p['doc_number']}")

if p.get("warning"):
    st.warning(p["warning"])

with st.expander("出荷明細を確認", expanded=True):
    st.dataframe(
        [{"出荷日": r["date"], "品名": r["product"], "kg": r["kg"],
          "伝票番号": r["denpyo"]} for r in p["rows"]],
        use_container_width=True, hide_index=True)

# ---- PDFプレビュー / ダウンロード ----
pdf_bytes = base64.b64decode(p["pdf_b64"])
st.download_button("📄 請求書PDFを開く / 保存", data=pdf_bytes,
                   file_name=p["pdf_name"], mime="application/pdf",
                   use_container_width=True)
b64 = p["pdf_b64"]
st.markdown(
    f'<iframe src="data:application/pdf;base64,{b64}" '
    f'width="100%" height="520" style="border:1px solid #ddd;border-radius:8px"></iframe>',
    unsafe_allow_html=True)

st.divider()
st.markdown(f"#### この内容で **{gc.CUSTOMER_EMAIL}** へ送信しますか？")

if st.session_state.get("_granada_confirm"):
    cc1, cc2 = st.columns(2)
    if cc1.button("✅ はい、送信する", type="primary", use_container_width=True):
        with st.spinner("送信中…"):
            r = gc.send_pending()
        if r.get("ok"):
            st.session_state.pop("_granada_confirm", None)
            st.success("送信しました。スマホにも完了通知を送りました。")
            st.balloons()
            st.rerun()
        else:
            st.error(f"送信できませんでした：{r.get('msg')}")
    if cc2.button("やめる", use_container_width=True):
        st.session_state.pop("_granada_confirm", None)
        st.rerun()
else:
    if st.button("送信する", type="primary", use_container_width=True):
        st.session_state["_granada_confirm"] = True
        st.rerun()
