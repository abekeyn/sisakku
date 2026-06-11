# -*- coding: utf-8 -*-
"""阿部農園 精米・発送管理システム（単一アプリ・タブナビ型）。

画面構成（受注管理システムの定石にならったワークキュー型）：
- 🏠 ホーム : 今日やること。精米キュー → 発送キュー → 出荷 まで一画面で完結
- 📋 注文   : 全注文の検索・編集・削除・状態変更
- 👤 顧客   : 顧客マスタの検索・追加・編集・削除
- ⚙ 設定   : 送り主・商品・CSV取込・BASE連携・データ管理
追加・編集はすべてモーダル（ダイアログ）で行い、画面遷移しない。
"""
from datetime import date, datetime

import pandas as pd
import streamlit as st

from lib import base_api, bootstrap, db, exporter, komeful, logic, seed, ui, yamato

ui.setup_page()
bootstrap.ensure_initialized()

TIME_CODES = {
    "指定なし": "0000", "午前中": "0812", "14-16時": "1416",
    "16-18時": "1618", "18-20時": "1820", "19-21時": "1921",
}
CODE_TO_LABEL = {v: k for k, v in TIME_CODES.items()}
CHANNEL_OPTS = {"LINE": "line", "コメフル": "komeful", "BASE": "base", "手入力": "manual"}


def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(str(s), "%Y/%m/%d").date()
    except (ValueError, TypeError):
        return date.today()


def _unshipped(orders):
    return [o for o in orders if o["status"] in ("pending", "milled")]


# ===========================================================================
# ダイアログ（モーダル）
# ===========================================================================
@st.dialog("➕ 注文を追加")
def dlg_add_order():
    customers = db.list_customers()
    products = db.list_products()
    prod_labels = {p["name"]: p["id"] for p in products}

    tab_old, tab_new = st.tabs(["👤 既存のお客様", "🆕 新規のお客様"])

    with tab_old:
        if not customers:
            st.info("顧客が未登録です。「新規のお客様」から追加してください。")
        else:
            cust_labels = {f'{c["name"]}（{c["address"]}）': c["id"] for c in customers}
            sel = st.selectbox("お客様（名前で検索できます）", list(cust_labels))
            c1, c2 = st.columns(2)
            prod = c1.selectbox("商品", list(prod_labels))
            qty = c2.number_input("数量", min_value=1, value=1)
            c3, c4 = st.columns(2)
            ch = c3.selectbox("経路", list(CHANNEL_OPTS))
            ship = c4.date_input("出荷予定日", value=date.today())
            mill = st.number_input("複合の精米kg（複合商品のみ・1個あたり）", min_value=0.0, value=0.0, step=1.0)
            note = st.text_input("メモ（任意）")
            if st.button("追加する", type="primary", use_container_width=True):
                db.add_order({
                    "customer_id": cust_labels[sel], "product_id": prod_labels[prod],
                    "qty": int(qty), "channel": CHANNEL_OPTS[ch],
                    "order_date": date.today().strftime("%Y/%m/%d"),
                    "ship_date": ship.strftime("%Y/%m/%d"),
                    "delivery_date": "", "delivery_time": "",
                    "milling_kg_override": mill if mill > 0 else None,
                    "note": note, "status": "pending", "external_id": "", "dispatch_ref": "",
                })
                st.rerun()

    with tab_new:
        n1, n2 = st.columns(2)
        name = n1.text_input("お届け先名 *")
        kana = n2.text_input("フリガナ")
        n3, n4 = st.columns([1, 2])
        zipc = n3.text_input("郵便番号 *", placeholder="9630211")
        addr = n4.text_input("住所 *")
        n5, n6 = st.columns(2)
        addr2 = n5.text_input("建物名・部屋番号")
        tel = n6.text_input("電話番号 *")
        o1, o2 = st.columns(2)
        prod_n = o1.selectbox("商品", list(prod_labels), key="np")
        qty_n = o2.number_input("数量", min_value=1, value=1, key="nq")
        o3, o4 = st.columns(2)
        ch_n = o3.selectbox("経路", list(CHANNEL_OPTS), key="nc")
        ship_n = o4.date_input("出荷予定日", value=date.today(), key="ns")
        if st.button("お客様＋注文を追加", type="primary", use_container_width=True, key="nbtn"):
            if not (name and zipc and addr and tel):
                st.error("名前・郵便番号・住所・電話番号は必須です。")
            else:
                cid = db.upsert_customer({
                    "name": name, "kana": kana, "tel": tel, "zip": zipc,
                    "address": addr, "address2": addr2, "honorific": "様",
                })
                db.add_order({
                    "customer_id": cid, "product_id": prod_labels[prod_n],
                    "qty": int(qty_n), "channel": CHANNEL_OPTS[ch_n],
                    "order_date": date.today().strftime("%Y/%m/%d"),
                    "ship_date": ship_n.strftime("%Y/%m/%d"),
                    "delivery_date": "", "delivery_time": "",
                    "milling_kg_override": None, "note": "",
                    "status": "pending", "external_id": "", "dispatch_ref": "",
                })
                st.rerun()


@st.dialog("✏️ 注文を編集")
def dlg_edit_order(o):
    st.markdown(f'**{o["customer_name"]} 様**　{o["product_name"]}')
    c1, c2 = st.columns(2)
    qty = c1.number_input("数量", min_value=1, value=int(o["qty"] or 1))
    ship = c2.date_input("出荷予定日", value=_parse_date(o["ship_date"]))
    c3, c4 = st.columns(2)
    ddate = c3.text_input("お届け日（空欄=指定なし）", o["delivery_date"] or "", placeholder="2026/06/15")
    dtime = c4.selectbox("時間帯", list(TIME_CODES),
                         index=list(TIME_CODES.values()).index(o["delivery_time"])
                         if o["delivery_time"] in TIME_CODES.values() else 0)
    mill = st.number_input(
        "複合の精米kg（1個あたり）", min_value=0.0, step=1.0,
        value=float(o["milling_kg_override"] or 0),
        help="複合商品のとき、1個あたり何kg精米するか。入力すると精米量に反映されます。",
    ) if o["category"] == "複合" else None
    note = st.text_input("メモ", o["note"] or "")
    b1, b2 = st.columns(2)
    if b1.button("保存", type="primary", use_container_width=True):
        db.update_order(o["id"], {
            "qty": int(qty), "ship_date": ship.strftime("%Y/%m/%d"),
            "delivery_date": ddate.strip(), "delivery_time": TIME_CODES[dtime],
            "milling_kg_override": (mill if (mill and mill > 0) else None) if o["category"] == "複合" else o["milling_kg_override"],
            "note": note,
        })
        st.rerun()
    if b2.button("🗑 この注文を削除", use_container_width=True):
        db.delete_order(o["id"])
        st.rerun()


@st.dialog("👤 お客様情報")
def dlg_customer(c=None):
    is_new = c is None
    c = c or {}
    n1, n2 = st.columns(2)
    name = n1.text_input("お届け先名 *", c.get("name", ""))
    kana = n2.text_input("フリガナ", c.get("kana", ""))
    n3, n4 = st.columns([1, 2])
    zipc = n3.text_input("郵便番号 *", c.get("zip", ""))
    addr = n4.text_input("住所 *", c.get("address", ""))
    n5, n6 = st.columns(2)
    addr2 = n5.text_input("建物名等", c.get("address2", ""))
    tel = n6.text_input("電話番号 *", c.get("tel", ""))
    company = st.text_input("会社・部門名", c.get("company", ""))
    if st.button("保存", type="primary", use_container_width=True):
        if not (name and zipc and addr):
            st.error("名前・郵便番号・住所は必須です。")
        else:
            data = {"name": name, "kana": kana, "tel": tel, "zip": zipc,
                    "address": addr, "address2": addr2, "company": company,
                    "honorific": "様"}
            if is_new:
                db.upsert_customer(data)
            else:
                db.update_customer(c["id"], data)
            st.rerun()


@st.dialog("📥 CSVから取り込む")
def dlg_csv_import():
    tab_b, tab_k = st.tabs(["🟢 BASE", "🟡 コメフル"])
    with tab_b:
        up = st.file_uploader("BASEの注文CSV", type=["csv"], key="csv_b")
        if up and st.button("取り込む", type="primary", key="btn_b"):
            r = base_api.import_base_csv(up.getvalue())
            if r.get("error"):
                st.error(r["error"])
            else:
                st.success(f"追加 {r['added']} 件／既存 {r['skipped']} 件")
    with tab_k:
        st.link_button("コメフル管理画面を開く", komeful.SELLER_URL, use_container_width=True)
        up2 = st.file_uploader("コメフルの注文CSV", type=["csv"], key="csv_k")
        if up2 and st.button("取り込む", type="primary", key="btn_k"):
            r = komeful.import_komeful_csv(up2.getvalue())
            if r.get("error"):
                st.error(r["error"])
            else:
                st.success(f"追加 {r['added']} 件／既存 {r['skipped']} 件")


@st.dialog("🚚 伝票番号を取り込んで出荷完了")
def dlg_confirm_shipment():
    st.caption(
        "ヤマトB2クラウドで送り状を**発行した後**、B2クラウドから「発行済データ」CSVを"
        "ダウンロードしてここに入れると：伝票番号を注文に記録 → 出荷完了 → "
        "**BASEには発送完了＋伝票番号を自動登録**します（お客様への発送メールに追跡番号が載ります）。"
    )

    # 直前の実行結果があれば表示
    if st.session_state.get("ship_result"):
        for m in st.session_state["ship_result"]:
            st.write(m)
        if st.button("閉じる", use_container_width=True):
            st.session_state.pop("ship_result", None)
            st.rerun()
        return

    up = st.file_uploader("B2クラウドの発行済データCSV", type=["csv"], key="trk_csv")
    if up is None:
        return

    import re as _re

    def _digits(s):
        return _re.sub(r"\D", "", str(s or ""))

    def _expected_item(o):
        n, q = o["yamato_name"], o["qty"] or 1
        return f"{n}×{q}" if q > 1 else n

    rows = yamato.parse_issued_for_tracking(up.getvalue())
    remaining = list(_unshipped(db.list_orders()))
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

    if matches:
        st.success(f"{len(matches)} 件の注文と照合できました")
        for r, o in matches:
            st.write(f'・{o["customer_name"]} 様（{o["product_name"]} ×{o["qty"]}）→ 伝票番号 **{r["tracking"]}**')
    if unmatched:
        st.warning("照合できなかった行：" +
                   "、".join(f'{r["name"]}（{r["tracking"]}）' for r in unmatched))
    if not matches:
        return

    if st.button(f"✅ {len(matches)}件を出荷完了にする（BASEにも反映）",
                 type="primary", use_container_width=True):
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
        msgs.insert(0, f"**{len(matches)} 件を出荷完了にしました。**")
        if komeful_flag:
            msgs.append(f"🛒 コメフルの注文は管理画面で出荷処理してください：{komeful.SELLER_URL}")
        st.session_state["ship_result"] = msgs
        st.rerun(scope="fragment")


# ===========================================================================
# 🏠 ホーム（今日やること）
# ===========================================================================
@st.fragment
def view_home():
    orders_all = db.list_orders()
    unshipped = _unshipped(orders_all)
    pending = [o for o in unshipped if o["status"] == "pending"]
    summary = logic.milling_summary(pending)

    # ---- クイックアクション ----
    a1, a2, a3 = st.columns(3)
    if a1.button("➕ 注文追加", use_container_width=True):
        dlg_add_order()
    if a2.button("🔄 BASE取込", use_container_width=True):
        cfg = db.get_setting("base_config") or {}
        if not cfg.get("refresh_token"):
            st.warning("設定タブでBASE連携を登録してください。")
        else:
            with st.spinner("BASEから未発送の注文を取得中..."):
                r = base_api.fetch_orders_via_api()
            if r.get("error"):
                st.error(r["error"])
            else:
                st.toast(f"BASE：未発送{r.get('target', 0)}件中、新規 {r['added']} 件を取込", icon="✅")
                st.rerun(scope="fragment")
    if a3.button("📥 CSV取込", use_container_width=True):
        dlg_csv_import()

    # ---- 今日のサマリ（kg中心） ----
    genmai = [o for o in pending if o["category"] == "玄米"]
    genmai_kg = sum((o["weight_kg"] or 0) * (o["qty"] or 1) for o in genmai)
    m1, m2, m3 = st.columns(3)
    m1.metric("精米", f"{summary['total_kg']:g} kg")
    m2.metric("玄米", f"{genmai_kg:g} kg", help="精米不要。そのまま用意してください")
    m3.metric("発送待ち", f"{len(unshipped)} 件")

    # ---- ① 精米・用意キュー ----
    ui.section("① 精米・用意する", "精米が終わったら「精米完了」。玄米・やさいは精米不要なので、そのまま用意してください")
    groups: dict[str, dict] = {}
    for o in pending:
        qty = o["qty"] or 1
        if o["category"] == "複合" and o["milling_kg_override"]:
            key = f'{o["product_name"]}（精米{o["milling_kg_override"]:g}kg/個）'
            kg = o["milling_kg_override"] * qty
        elif o["needs_milling"]:
            key = o["product_name"]
            kg = (o["weight_kg"] or 0) * qty
        else:
            continue
        g = groups.setdefault(key, {"ids": [], "qty": 0, "kg": 0.0})
        g["ids"].append(o["id"])
        g["qty"] += qty
        g["kg"] += kg

    # 精米不要（玄米・やさい等）の用意リスト
    prep_groups: dict[str, dict] = {}
    for o in pending:
        if o["needs_milling"] or o["category"] == "複合":
            continue
        qty = o["qty"] or 1
        g = prep_groups.setdefault(o["product_name"], {"qty": 0, "kg": 0.0, "cat": o["category"]})
        g["qty"] += qty
        g["kg"] += (o["weight_kg"] or 0) * qty

    if not groups and not prep_groups:
        st.success("精米・用意するものはありません 🎉")
    for key, g in sorted(groups.items(), key=lambda x: -x[1]["kg"]):
        c1, c2 = st.columns([3, 1])
        c1.markdown(
            f'<div class="mill-row"><span class="mill-big">{key}</span>'
            f'<span><b>{g["kg"]:g}kg</b>（×{g["qty"]}）</span></div>',
            unsafe_allow_html=True,
        )
        if c2.button("精米完了", key=f'mill{key}', use_container_width=True):
            db.update_order_status(g["ids"], "milled")
            st.toast(f"{key} を精米済みにしました", icon="🌾")
            st.rerun(scope="fragment")
    for key, g in sorted(prep_groups.items(), key=lambda x: -x[1]["kg"]):
        kg_txt = f'<b>{g["kg"]:g}kg</b>（×{g["qty"]}）' if g["kg"] else f'×{g["qty"]}'
        st.markdown(
            f'<div class="mill-row"><span class="mill-big">{key}</span>'
            f'<span>{kg_txt}　'
            f'<span class="chip" style="color:#475569;background:#E2E8F0">精米不要</span></span></div>',
            unsafe_allow_html=True,
        )

    # 複合で精米量未入力のもの
    checks = [o for o in pending
              if o["category"] == "複合" and not o["milling_kg_override"]]
    for o in checks:
        c1, c2 = st.columns([3, 1])
        c1.warning(f'⚠️ {o["customer_name"]}様の「{o["product_name"]}」は精米kgが未入力です')
        if c2.button("入力する", key=f'fix{o["id"]}', use_container_width=True):
            dlg_edit_order(o)

    # ---- ② 発送キュー ----
    st.markdown('<hr class="brand-rule"/>', unsafe_allow_html=True)
    ui.section("② 発送する", "出荷する注文を選んで、CSV作成 → 出荷完了")

    if not unshipped:
        st.info("発送待ちの注文はありません。")
        return

    sel_ids = []
    for o in unshipped:
        c1, c2, c3 = st.columns([0.5, 5.2, 0.8])
        checked = c1.checkbox(" ", value=True, key=f'sel{o["id"]}',
                              label_visibility="collapsed")
        if checked:
            sel_ids.append(o["id"])
        c2.markdown(ui.order_card(o), unsafe_allow_html=True)
        with c3.popover("⋮"):
            if st.button("✏️ 編集", key=f'e{o["id"]}', use_container_width=True):
                dlg_edit_order(o)
            if o["status"] == "milled":
                if st.button("↩ 精米待ちに戻す", key=f'um{o["id"]}', use_container_width=True):
                    db.update_order_status([o["id"]], "pending")
                    st.rerun(scope="fragment")

    # 出荷オプション
    o1, o2 = st.columns(2)
    ship_d = o1.date_input("出荷日", value=date.today(), key="bulk_ship")
    time_sel = o2.selectbox("時間帯", ["注文の指定どおり"] + list(TIME_CODES), key="bulk_time")

    sender = db.get_setting("sender") or {}
    if not sender.get("name"):
        st.warning("送り主が未設定です（設定タブで登録してください）")

    b1, b2 = st.columns(2)
    if b1.button(f"📄 ヤマトCSV作成（{len(sel_ids)}件）", type="primary",
                 use_container_width=True, disabled=not sel_ids):
        targets = [o for o in unshipped if o["id"] in set(sel_ids)]
        for o in targets:
            upd = {"ship_date": ship_d.strftime("%Y/%m/%d")}
            if time_sel != "注文の指定どおり":
                upd["delivery_time"] = TIME_CODES[time_sel]
            db.update_order(o["id"], upd)
        targets = [o for o in _unshipped(db.list_orders()) if o["id"] in set(sel_ids)]
        csv_bytes = yamato.export_csv(targets, sender)
        st.session_state["csv_data"] = csv_bytes
        res = exporter.save_or_reserve(csv_bytes)
        if res["mode"] == "saved":
            st.success(f"デスクトップの『ヤマト出荷CSV』に保存しました\n\n📄 {res['path']}")
        else:
            st.success("CSVを作成しました。下のボタンでダウンロードできます。"
                       "（PC起動中なら数秒でデスクトップにも自動保存されます）")

    if b2.button(f"✅ 出荷完了（{len(sel_ids)}件）", use_container_width=True,
                 disabled=not sel_ids,
                 help="BASEの注文はAPIで自動的に発送完了になります"):
        targets = [o for o in unshipped if o["id"] in set(sel_ids)]
        msgs, komeful_flag = [], False
        for o in targets:
            if o["channel"] == "base":
                ok, msg = base_api.dispatch_order(o)
                msgs.append(("✅" if ok else "⚠️") + f' {o["customer_name"]}様：{msg}')
            elif o["channel"] == "komeful":
                komeful_flag = True
        db.update_order_status(sel_ids, "shipped")
        st.success(f"{len(sel_ids)} 件を出荷済みにしました。")
        for m in msgs:
            st.write(m)
        if komeful_flag:
            st.link_button("🛒 コメフルの出荷処理を開く", komeful.SELLER_URL, use_container_width=True)
        st.rerun(scope="fragment")

    if st.session_state.get("csv_data"):
        st.download_button(
            "⬇️ 送り状CSVをダウンロード", data=st.session_state["csv_data"],
            file_name=exporter.make_filename(), mime="text/csv",
            use_container_width=True,
        )
        st.caption("ヤマトB2クラウド「送り状発行 → 外部データ取込」にアップロードすると印刷できます。")

    # ---- ③ 印刷後：伝票番号の取込 ----
    st.markdown('<hr class="brand-rule"/>', unsafe_allow_html=True)
    ui.section("③ 印刷後：伝票番号の取込",
               "B2クラウドで印刷したら「発行済データ」CSVをここへ。伝票番号を記録して出荷完了し、BASEにも自動登録します")
    if st.button("🚚 発行済データを取り込んで出荷完了", use_container_width=True):
        dlg_confirm_shipment()


# ===========================================================================
# 📋 注文（一覧・検索・編集）
# ===========================================================================
@st.fragment
def view_orders():
    f1, f2 = st.columns([2, 3])
    flt = f1.segmented_control(
        "状態", ["未出荷", "出荷済み", "すべて"], default="未出荷",
        key="oflt", label_visibility="collapsed",
    ) or "未出荷"
    q = f2.text_input("検索", placeholder="🔍 お客様名・商品名で検索",
                      key="oq", label_visibility="collapsed")

    orders = db.list_orders()
    if flt == "未出荷":
        orders = _unshipped(orders)
    elif flt == "出荷済み":
        orders = [o for o in orders if o["status"] == "shipped"]
    if q.strip():
        key = logic.normalize_text(q)
        orders = [o for o in orders
                  if key in logic.normalize_text(o["customer_name"])
                  or key in logic.normalize_text(o["product_name"])]

    st.caption(f"{len(orders)} 件")
    for o in orders[:80]:
        c1, c2 = st.columns([6, 0.8])
        meta = f'<div class="o-meta">注文日 {o["order_date"] or "-"}／出荷予定 {o["ship_date"] or "-"}</div>'
        c1.markdown(ui.order_card(o, meta), unsafe_allow_html=True)
        with c2.popover("⋮"):
            if o["status"] != "shipped":
                if st.button("✏️ 編集", key=f'oe{o["id"]}', use_container_width=True):
                    dlg_edit_order(o)
                if st.button("✅ 出荷済みにする", key=f'os{o["id"]}', use_container_width=True):
                    db.update_order_status([o["id"]], "shipped")
                    st.rerun(scope="fragment")
            else:
                if st.button("↩ 未出荷に戻す", key=f'ob{o["id"]}', use_container_width=True):
                    db.update_order_status([o["id"]], "pending")
                    st.rerun(scope="fragment")
            if st.button("🗑 削除", key=f'od{o["id"]}', use_container_width=True):
                db.delete_order(o["id"])
                st.rerun(scope="fragment")
    if len(orders) > 80:
        st.caption(f"…ほか {len(orders)-80} 件（検索で絞り込めます）")


# ===========================================================================
# 👤 顧客（一覧・追加・編集）
# ===========================================================================
@st.fragment
def view_customers():
    c1, c2 = st.columns([3, 1.2])
    q = c1.text_input("検索", placeholder="🔍 名前・住所で検索",
                      key="cq", label_visibility="collapsed")
    if c2.button("➕ 新規追加", use_container_width=True):
        dlg_customer(None)

    customers = db.list_customers()
    if q.strip():
        key = logic.normalize_text(q)
        customers = [c for c in customers
                     if key in logic.normalize_text(c["name"])
                     or key in logic.normalize_text(c["address"])]

    order_counts: dict[int, int] = {}
    for o in db.list_orders():
        order_counts[o["customer_id"]] = order_counts.get(o["customer_id"], 0) + 1

    st.caption(f"{len(customers)} 名")
    for c in customers:
        k1, k2 = st.columns([6, 0.8])
        n = order_counts.get(c["id"], 0)
        k1.markdown(
            f'<div class="o-card"><span class="o-name">{c["name"]} 様</span>　'
            f'<span class="o-meta">注文 {n} 回</span>'
            f'<div class="o-meta">〒{c["zip"]}　{c["address"]}{c["address2"] or ""}　📞 {c["tel"]}</div></div>',
            unsafe_allow_html=True,
        )
        with k2.popover("⋮"):
            if st.button("✏️ 編集", key=f'ce{c["id"]}', use_container_width=True):
                dlg_customer(c)
            if n == 0:
                if st.button("🗑 削除", key=f'cd{c["id"]}', use_container_width=True):
                    db.delete_customer(c["id"])
                    st.rerun(scope="fragment")
            else:
                st.caption("注文履歴があるため削除不可")


# ===========================================================================
# ⚙ 設定
# ===========================================================================
def view_settings():
    tab_sender, tab_prod, tab_base, tab_data = st.tabs(
        ["📮 送り主", "🍚 商品", "🔗 BASE連携", "🗃 データ"]
    )

    with tab_sender:
        s = db.get_setting("sender") or {}
        with st.form("sender_form"):
            c1, c2 = st.columns(2)
            name = c1.text_input("ご依頼主名", s.get("name", ""))
            kana = c2.text_input("フリガナ", s.get("kana", ""))
            c3, c4 = st.columns([1, 2])
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
                st.success("保存しました。")

    with tab_prod:
        st.caption("「精米が必要」の商品だけが精米量に加算されます。『品名(送り状用)』が送り状に印字されます。")
        products = db.list_products(active_only=False)
        pdf = pd.DataFrame([{
            "商品名": p["name"], "区分": p["category"], "重量kg": p["weight_kg"],
            "精米が必要": bool(p["needs_milling"]), "品名(送り状用)": p["yamato_name"],
            "並び順": p["sort_order"], "有効": bool(p["active"]),
        } for p in products])
        edited = st.data_editor(
            pdf, use_container_width=True, hide_index=True, num_rows="dynamic",
            column_config={
                "区分": st.column_config.SelectboxColumn("区分", options=["精米", "玄米", "複合", "その他"]),
                "精米が必要": st.column_config.CheckboxColumn("精米が必要"),
                "有効": st.column_config.CheckboxColumn("有効"),
            },
            key="prod_editor",
        )
        if st.button("💾 商品を保存", type="primary"):
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
            st.success("保存しました。")
            st.rerun()

    with tab_base:
        cfg = db.get_setting("base_config") or {}
        if cfg.get("refresh_token"):
            st.success("✅ BASE連携は設定済みです（自動取込・自動出荷が使えます）")
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
                st.success("保存しました。")

    with tab_data:
        ui.section("顧客データの取込", "ヤマトB2クラウドの発行済データCSVから顧客を登録します")
        up = st.file_uploader("発行済データCSV", type=["csv"], key="reimport")
        hist = st.checkbox("過去の注文も記録する（出荷済み扱い）", value=False)
        if up is not None and st.button("📥 取り込む"):
            r = seed.import_issued_csv(up.getvalue(), import_history=hist)
            db.clear_cache()
            st.success(f"顧客 +{r['customers']} 名／注文 +{r['orders']} 件")

        st.divider()
        ui.section("ログアウト")
        if st.button("🚪 ログアウト"):
            st.session_state.pop("authed", None)
            st.rerun()

        st.divider()
        ui.section("全データの初期化", "注文・顧客・設定をすべて消去します（元に戻せません）")
        confirm = st.text_input('「リセット」と入力すると実行できます', "")
        if st.button("🗑 全データをリセット", disabled=(confirm != "リセット")):
            db.reset_all()
            st.success("初期化しました。再読み込みすると初期状態になります。")


# ===========================================================================
# ルーティング
# ===========================================================================
view = ui.render_nav()
if view == "ホーム":
    view_home()
elif view == "注文":
    view_orders()
elif view == "顧客":
    view_customers()
else:
    view_settings()
