# -*- coding: utf-8 -*-
"""阿部農園 精米・発送管理システム（単一アプリ・タブナビ型）。

画面構成（受注管理システムの定石にならったワークキュー型）：
- 🏠 ホーム : 今日やること。精米キュー → 発送キュー → 出荷 まで一画面で完結
- 📋 注文   : 全注文の検索・編集・削除・状態変更
- 👤 顧客   : 顧客マスタの検索・追加・編集・削除
- ⚙ 設定   : 送り主・商品・CSV取込・BASE連携・データ管理
追加・編集はすべてモーダル（ダイアログ）で行い、画面遷移しない。
"""
from datetime import date, datetime, timedelta, timezone

import altair as alt
import pandas as pd
import streamlit as st

# 日本時間（クラウドのサーバーは米国時間のため、日付は必ずJSTで扱う）
JST = timezone(timedelta(hours=9))


def today() -> date:
    return datetime.now(JST).date()


def now_iso() -> str:
    return datetime.now(JST).isoformat()

from lib import (analytics, base_api, bootstrap, db, exporter, komeful, logic,
                 seed, shipping, ui, yamato)

ui.setup_page()
bootstrap.ensure_initialized()

TIME_CODES = {
    "指定なし": "0000", "午前中": "0812", "14-16時": "1416",
    "16-18時": "1618", "18-20時": "1820", "19-21時": "1921",
}
CODE_TO_LABEL = {v: k for k, v in TIME_CODES.items()}
CHANNEL_OPTS = {"LINE": "line", "コメフル": "komeful", "BASE": "base", "手入力": "manual"}
# 集荷の時間帯（ヤマト集荷依頼ページの実オプションに一致させる）
PICKUP_TIMES = ["指定なし", "13時まで", "14時から16時まで",
                "16時から18時まで", "17時から18時30分まで"]

# BASE発送通知の既定文面（○○ はお客様名に自動置換）
DEFAULT_DISPATCH_MESSAGE = (
    "○○様\n\n"
    "このたびは、ご購入いただきありがとうございます。\n"
    "発送手配が完了いたしました。\n"
    "※本通知後24時間以内の出荷となるため\n"
    "　追跡ができるまでお時間をいただく場合がございます。\n\n"
    "送り状番号は記載のものをご確認ください。\n\n"
    "到着まで今しばらくお待ちくださいませ！"
)


def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(str(s), "%Y/%m/%d").date()
    except (ValueError, TypeError):
        return today()


def _unshipped(orders):
    return [o for o in orders if o["status"] in ("pending", "milled")]


# ===========================================================================
# ダイアログ（モーダル）
# ===========================================================================
@st.dialog("注文を追加")
def dlg_add_order():
    customers = db.list_customers()
    products = db.list_products()
    prod_labels = {p["name"]: p["id"] for p in products}

    tab_old, tab_new = st.tabs(["既存のお客様", "新規のお客様"])

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
            ship = c4.date_input("出荷予定日", value=today())
            mill = st.number_input("複合の精米kg（複合商品のみ・1個あたり）", min_value=0.0, value=0.0, step=1.0)
            note = st.text_input("メモ（任意）")
            handover = st.checkbox("手渡し（送り状・配送なし）", value=False,
                                   help="直接お渡しする注文。送り状の作成・印刷の対象外になります。")
            if st.button("追加する", type="primary", use_container_width=True):
                db.add_order({
                    "customer_id": cust_labels[sel], "product_id": prod_labels[prod],
                    "qty": int(qty), "channel": CHANNEL_OPTS[ch],
                    "order_date": today().strftime("%Y/%m/%d"),
                    "ship_date": ship.strftime("%Y/%m/%d"),
                    "delivery_date": "", "delivery_time": "",
                    "milling_kg_override": mill if mill > 0 else None,
                    "note": note, "status": "pending", "external_id": "", "dispatch_ref": "",
                    "handover": 1 if handover else 0,
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
        ship_n = o4.date_input("出荷予定日", value=today(), key="ns")
        handover_n = st.checkbox("手渡し（送り状・配送なし）", value=False, key="nh",
                                 help="直接お渡しする注文。送り状の作成・印刷の対象外になります。")
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
                    "order_date": today().strftime("%Y/%m/%d"),
                    "ship_date": ship_n.strftime("%Y/%m/%d"),
                    "delivery_date": "", "delivery_time": "",
                    "milling_kg_override": None, "note": "",
                    "status": "pending", "external_id": "", "dispatch_ref": "",
                    "handover": 1 if handover_n else 0,
                })
                st.rerun()


@st.dialog("注文を編集")
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
    handover = st.checkbox("手渡し（送り状・配送なし）", value=bool(o.get("handover")),
                           help="直接お渡しする注文。送り状の作成・印刷の対象外になります。")
    b1, b2 = st.columns(2)
    if b1.button("保存", type="primary", use_container_width=True):
        db.update_order(o["id"], {
            "qty": int(qty), "ship_date": ship.strftime("%Y/%m/%d"),
            "delivery_date": ddate.strip(), "delivery_time": TIME_CODES[dtime],
            "milling_kg_override": (mill if (mill and mill > 0) else None) if o["category"] == "複合" else o["milling_kg_override"],
            "note": note, "handover": 1 if handover else 0,
        })
        st.rerun()
    if b2.button("✕ この注文を削除", use_container_width=True):
        db.delete_order(o["id"])
        st.rerun()


@st.dialog("お客様情報")
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


@st.dialog("CSVから取り込む")
def dlg_csv_import():
    tab_b, tab_k = st.tabs(["BASE", "コメフル"])
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


@st.dialog("伝票番号を取り込んで出荷完了")
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

    rows = yamato.parse_issued_for_tracking(up.getvalue())
    matches, unmatched = shipping.match_tracking(rows, _unshipped(db.list_orders()))

    if matches:
        st.success(f"{len(matches)} 件の注文と照合できました")
        for r, o in matches:
            st.write(f'・{o["customer_name"]} 様（{o["product_name"]} ×{o["qty"]}）→ 伝票番号 **{r["tracking"]}**')
    if unmatched:
        st.warning("照合できなかった行：" +
                   "、".join(f'{r["name"]}（{r["tracking"]}）' for r in unmatched))
    if not matches:
        return

    if st.button(f"✓ {len(matches)}件を出荷完了にする（BASEにも反映）",
                 type="primary", use_container_width=True):
        result = shipping.confirm_shipments(matches)
        msgs = [f"**{result['shipped']} 件を出荷完了にしました。**"] + result["messages"]
        st.session_state["ship_result"] = msgs
        st.rerun(scope="fragment")


# ===========================================================================
# 🏠 ホーム（今日やること）
# ===========================================================================
def _start_agent(req_key, prog_key, res_key, title, payload_key=None, payload=None):
    """PC常駐エージェントに作業を指示し、進捗バーの監視を開始する。"""
    db.set_setting(res_key, {"pending": True})
    db.set_setting(prog_key, {"pct": 1, "step": "PCに指示を送信しました"})
    if payload_key is not None:
        db.set_setting(payload_key, payload)
    db.set_setting(req_key, now_iso())
    st.session_state.pop("agent_last", None)
    st.session_state["agent_watch"] = {"prog": prog_key, "res": res_key, "title": title}
    st.rerun()


def _cloud_issue_and_print(csv_bytes: bytes) -> None:
    """PC不要ルート：クラウドでB2発行→PDF取得→プリンタへメール送信して印刷。"""
    from lib import b2_fetch, mailer
    ok, why = mailer.is_configured()
    if not ok:
        st.error(f"メール印刷の設定が未完了です（{why}）。設定 → 印刷 で登録してください。")
        return
    with st.status("クラウドで発行・印刷しています…", expanded=True) as status:
        bar = st.progress(0)

        def cb(p, s):
            try:
                bar.progress(min(int(p), 100) / 100, text=s)
            except Exception:  # noqa: BLE001
                pass

        try:
            r = b2_fetch.issue_and_print(csv_bytes, progress=cb)
        except Exception as e:  # noqa: BLE001
            status.update(label="送り状の発行に失敗", state="error")
            st.error(f"発行に失敗しました：{e}")
            return
        if not r.get("pdf"):
            status.update(label="PDFを取得できませんでした", state="error")
            st.warning((r.get("message", "") or "") + " PDFが取得できませんでした。")
            return
        cb(97, "プリンタへPDFを送信中")
        mok, mmsg = mailer.send_pdf_to_printer(r["pdf"], filename="soujou.pdf")
        if mok:
            cb(100, "完了")
            status.update(label="完了：プリンタへ送信しました", state="complete")
            st.success(f"{r.get('message','')} ／ {mmsg}")
        else:
            status.update(label="メール送信に失敗", state="error")
            st.error(mmsg)
            st.download_button("送り状PDFをダウンロード", data=r["pdf"],
                               file_name="soujou.pdf", mime="application/pdf",
                               use_container_width=True)


def _auto_sync_history():
    """ログイン後、1日1回だけヤマト発送履歴の自動取り込みをPCに指示する。

    実際の取得はPC常駐エージェントが行う（PC起動＋ヤマト利用時間が必要）。
    重い処理なので1日1回に制限。結果は顧客タブ等で確認できる。
    """
    if st.session_state.get("_hist_auto_done"):
        return
    st.session_state["_hist_auto_done"] = True
    if db.get_setting("b2_history_auto_date") == today().isoformat():
        return  # 今日はもう自動取得を指示済み
    db.set_setting("b2_history_auto_date", today().isoformat())
    db.set_setting("b2_history_result", {"pending": True})
    db.set_setting("b2_history_progress", {"pct": 1, "step": "ログイン時の自動取得"})
    db.set_setting("b2_history_request", now_iso())
    st.toast("発送履歴をヤマトから自動取得します（PC起動時・数分で顧客に反映）", icon="📥")


@st.fragment(run_every=2)
def _agent_progress():
    """実行中はライブ進捗バー、完了後は結果を表示する（2秒ごとに自動更新）。"""
    w = st.session_state.get("agent_watch")
    if w:
        res = db.get_setting_live(w["res"])
        if res and not res.get("pending"):
            st.session_state["agent_last"] = {"title": w["title"], "res": res}
            st.session_state.pop("agent_watch", None)
        else:
            # 開始直後の1回だけ最上部へスクロールして進捗バーを見せる
            if st.session_state.get("_scrolled_for") != w.get("res"):
                st.session_state["_scrolled_for"] = w.get("res")
                import streamlit.components.v1 as _components
                _components.html(
                    "<script>try{var d=window.parent.document;"
                    "var m=d.querySelector('[data-testid=stMain]')||d.querySelector('section.main');"
                    "if(m){m.scrollTo({top:0,behavior:'smooth'});}"
                    "else{window.parent.scrollTo({top:0,behavior:'smooth'});}}"
                    "catch(e){}</script>", height=0)
            prog = db.get_setting_live(w["prog"]) or {}
            pct = max(1, min(99, int(prog.get("pct", 1))))
            st.markdown(f'<div class="prog-title">{w["title"]}</div>',
                        unsafe_allow_html=True)
            st.progress(pct / 100, text=f'{prog.get("step", "準備中")}（{pct}%）')
            st.caption("PCで自動処理中です。完了まで数十秒〜1分ほどお待ちください。")
            return
    last = st.session_state.get("agent_last")
    if last:
        res = last["res"]
        ok = res.get("ok")
        box = st.success if ok else st.warning
        box(("✅ " if ok else "⚠️ ") + f'{last["title"]}：{res.get("summary") or ""}')


@st.fragment
def view_home():
    orders_all = db.list_orders()
    unshipped = _unshipped(orders_all)
    pending = [o for o in unshipped if o["status"] == "pending"]
    summary = logic.milling_summary(pending)

    # ---- 今日のようす（サマリ） ----
    genmai = [o for o in pending if o["category"] == "玄米"]
    genmai_kg = sum((o["weight_kg"] or 0) * (o["qty"] or 1) for o in genmai)
    m1, m2, m3 = st.columns(3)
    m1.metric("精米", f"{summary['total_kg']:g} kg")
    m2.metric("玄米", f"{genmai_kg:g} kg", help="精米不要。そのまま用意してください")
    m3.metric("発送待ち", f"{len(unshipped)} 件")

    # ======= STEP 1｜注文を集める =======
    ui.step(1, "注文を集める",
            "BASEはボタン1つで自動取込。LINE・コメフルの注文は手で追加します", first=True)
    a1, a2, a3 = st.columns(3)
    if a1.button("⟳ BASE取込", use_container_width=True):
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
    if a2.button("＋ 注文追加", use_container_width=True):
        dlg_add_order()
    if a3.button("CSV取込", use_container_width=True):
        dlg_csv_import()

    # ======= STEP 2｜精米する =======
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

    checks = [o for o in pending
              if o["category"] == "複合" and not o["milling_kg_override"]]
    ui.step(2, "精米する",
            "精米が終わったら「精米完了」→ 発送待ちに移ります。玄米・やさいは精米不要なので、そのまま用意してください",
            done=bool(unshipped) and not groups and not checks)

    if not groups and not prep_groups and not checks:
        if unshipped:
            st.success("精米・用意は完了しています。次は ❸ 送り状づくりへ")
        else:
            st.caption("注文を取り込むと、ここに精米する量が表示されます。")
    for key, g in sorted(groups.items(), key=lambda x: -x[1]["kg"]):
        c1, c2 = st.columns([3, 1])
        c1.markdown(
            f'<div class="mill-row"><span class="mill-big">{key}</span>'
            f'<span><b>{g["kg"]:g}kg</b>（×{g["qty"]}）</span></div>',
            unsafe_allow_html=True,
        )
        if c2.button("精米完了", key=f'mill{key}', use_container_width=True):
            db.update_order_status(g["ids"], "milled")
            st.toast(f"{key} を精米済みにしました", icon="✅")
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
    for o in checks:
        c1, c2 = st.columns([3, 1])
        c1.warning(f'⚠️ {o["customer_name"]}様の「{o["product_name"]}」は精米kgが未入力です')
        if c2.button("入力する", key=f'fix{o["id"]}', use_container_width=True):
            dlg_edit_order(o)

    # ======= STEP 3｜送り状を作る =======
    ui.step(3, "送り状を作る（ヤマト）",
            "出荷する注文を選んで「ヤマトCSV作成」→ B2クラウドに取り込んで印刷します")

    if not unshipped:
        st.caption("発送待ちの注文はありません。")
        return

    ship_orders = [o for o in unshipped if not o.get("handover")]
    hand_orders = [o for o in unshipped if o.get("handover")]

    if ship_orders:
        st.caption("出荷する注文（チェックを外すと今回の送り状から除外されます）")
    else:
        st.caption("送り状が必要な注文はありません（手渡しのみ）。")
    sel_ids = []
    for o in ship_orders:
        c1, c2, c3 = st.columns([0.5, 4.7, 1.1])
        checked = c1.checkbox(" ", value=True, key=f'sel{o["id"]}',
                              label_visibility="collapsed")
        if checked:
            sel_ids.append(o["id"])
        c2.markdown(ui.order_card(o), unsafe_allow_html=True)
        with c3.popover("操作", use_container_width=True):
            if st.button("✎ 編集", key=f'e{o["id"]}', use_container_width=True):
                dlg_edit_order(o)
            if o["status"] == "milled":
                if st.button("↩ 精米待ちに戻す", key=f'um{o["id"]}', use_container_width=True):
                    db.update_order_status([o["id"]], "pending")
                    st.rerun(scope="fragment")

    # お届け日時の指定（任意）。出荷予定日は自動で今日になる
    with st.expander("お届け日時を指定する（任意・配達日時の指定）"):
        st.caption("お客様への配達希望日・時間帯です（送り状に印字）。"
                   "指定しなければ各注文の設定のまま。出荷予定日は自動で今日になります"
                   "（※ステップ5の“集荷時間帯＝ヤマトが取りに来る時間”とは別物です）。")
        o1, o2 = st.columns(2)
        deliv_d = o1.date_input("お届け日（配達指定日）", value=None,
                                min_value=today(), key="bulk_deliv")
        time_sel = o2.selectbox("お届け時間帯（配達指定）",
                                ["注文の指定どおり"] + list(TIME_CODES), key="bulk_time")

    sender = db.get_setting("sender") or {}
    if not sender.get("name"):
        st.warning("送り主が未設定です（設定タブで登録してください）")

    def _build_csv_for_selected():
        targets = [o for o in unshipped if o["id"] in set(sel_ids)]
        for o in targets:
            upd = {"ship_date": today().strftime("%Y/%m/%d")}  # 出荷予定日は今日
            if deliv_d:
                upd["delivery_date"] = deliv_d.strftime("%Y/%m/%d")
            if time_sel != "注文の指定どおり":
                upd["delivery_time"] = TIME_CODES[time_sel]
            db.update_order(o["id"], upd)
        targets = [o for o in _unshipped(db.list_orders()) if o["id"] in set(sel_ids)]
        return yamato.export_csv(targets, sender)

    # B2で発行して自動印刷（PCの常駐エージェントが実行）
    if st.button(f"B2で送り状を発行して自動印刷（{len(sel_ids)}件）", type="primary",
                 use_container_width=True, disabled=not sel_ids,
                 help="PCの常駐プログラムがB2クラウドに送り状を発行し、PDFを既定プリンタへ自動印刷します（PCとプリンタが起動している必要があります）"):
        import base64
        csv_bytes = _build_csv_for_selected()
        st.session_state["csv_data"] = csv_bytes
        db.set_setting("b2_print_csv", base64.b64encode(csv_bytes).decode())
        _start_agent("b2_print_request", "b2_print_progress", "b2_print_result",
                     "送り状の発行・自動印刷")

    pr = db.get_setting("b2_print_result")
    if pr and not pr.get("pending"):
        st.caption(("✓ " if pr.get("ok") else "⚠ ") + f'前回の発行・印刷（{pr.get("at","")}）：{pr.get("summary","")}')

    # ☁ クラウドで発行して印刷（PC不要）：メール印刷の設定が済んでいる時だけ表示
    from lib import mailer as _mailer
    if _mailer.is_configured()[0]:
        if st.button(f"☁ クラウドで発行して印刷（PC不要・{len(sel_ids)}件）",
                     use_container_width=True, disabled=not sel_ids,
                     help="このアプリ（クラウド）がB2で送り状を発行し、PDFをプリンタへメール送信して印刷します。PCは不要。"):
            _cloud_issue_and_print(_build_csv_for_selected())

    with st.expander("CSVだけ作る（手動でB2に取り込む／控え）"):
        if st.button(f"ヤマトCSVを作成（{len(sel_ids)}件）", use_container_width=True,
                     disabled=not sel_ids):
            csv_bytes = _build_csv_for_selected()
            st.session_state["csv_data"] = csv_bytes
            res = exporter.save_or_reserve(csv_bytes)
            if res["mode"] == "saved":
                st.success(f"デスクトップの『ヤマト出荷CSV』に保存しました\n\n{res['path']}")
            else:
                st.success("CSVを作成しました。下のボタンで保存できます。")
        if st.session_state.get("csv_data"):
            st.download_button(
                "↓ 送り状CSVをダウンロード", data=st.session_state["csv_data"],
                file_name=exporter.make_filename(), mime="text/csv",
                use_container_width=True,
            )

    # ===== 手渡しの注文（送り状なし）=====
    if hand_orders:
        ui.section("手渡しの注文（送り状なし）",
                   "直接お渡しする注文です。送り状の作成・印刷・配送はありません")
        for o in hand_orders:
            h1, h2 = st.columns([6, 1.1])
            h1.markdown(ui.order_card(o), unsafe_allow_html=True)
            with h2.popover("操作", use_container_width=True):
                if st.button("✎ 編集", key=f'he{o["id"]}', use_container_width=True):
                    dlg_edit_order(o)
                if o["status"] == "milled":
                    if st.button("↩ 精米待ちに戻す", key=f'hum{o["id"]}',
                                 use_container_width=True):
                        db.update_order_status([o["id"]], "pending")
                        st.rerun(scope="fragment")
        if st.button(f"🤝 手渡しで完了にする（{len(hand_orders)}件）",
                     use_container_width=True,
                     help="送り状なしで出荷済みにします。BASEの注文は発送完了も送ります。"):
            msgs, komeful_flag = [], False
            for o in hand_orders:
                if o["channel"] == "base":
                    ok, msg = base_api.dispatch_order(o)
                    msgs.append(("✓ " if ok else "⚠ ") + f'{o["customer_name"]}様：{msg}')
                elif o["channel"] == "komeful":
                    komeful_flag = True
            db.update_order_status([o["id"] for o in hand_orders], "shipped")
            st.success(f"{len(hand_orders)} 件を手渡し完了（出荷済み）にしました。")
            for m in msgs:
                st.write(m)
            if komeful_flag:
                st.link_button("コメフルの出荷処理を開く", komeful.SELLER_URL,
                               use_container_width=True)
            st.rerun(scope="fragment")

    # ======= STEP 4｜出荷を確定する =======
    ui.step(4, "出荷を確定する",
            "B2クラウドで印刷できたら押すだけ。伝票番号を取り込んで出荷完了にし、BASEにも自動反映します")

    if st.button("B2から伝票番号を取得して出荷完了", type="primary", use_container_width=True,
                 help="PCの常駐プログラムがB2クラウドに自動ログインして発行済データを取得し、照合→伝票番号記録→出荷完了→BASE反映まで自動処理します（PCが起動している必要があります）"):
        _start_agent("b2_fetch_request", "b2_fetch_progress", "b2_fetch_result",
                     "伝票番号の取得・出荷確定")

    b2res = db.get_setting("b2_fetch_result")
    if b2res and not b2res.get("pending"):
        icon = "✓" if b2res.get("ok") else "⚠"
        st.caption(f'{icon} 前回の自動取得（{b2res.get("at","")}）：{b2res.get("summary","")}')
        for m in b2res.get("messages", []):
            st.caption(m)

    with st.expander("その他の確定方法（自動取得が使えないとき）"):
        if st.button("発行済データCSVを手動で取り込む", use_container_width=True):
            dlg_confirm_shipment()
        if st.button(f"✓ 選択中の{len(sel_ids)}件を伝票番号なしで出荷完了",
                     use_container_width=True, disabled=not sel_ids,
                     help="伝票番号は記録されませんが、出荷済みにしてBASEへ発送完了を送ります"):
            targets = [o for o in unshipped if o["id"] in set(sel_ids)]
            msgs, komeful_flag = [], False
            for o in targets:
                if o["channel"] == "base":
                    ok, msg = base_api.dispatch_order(o)
                    msgs.append(("✓ " if ok else "⚠ ") + f' {o["customer_name"]}様：{msg}')
                elif o["channel"] == "komeful":
                    komeful_flag = True
            db.update_order_status(sel_ids, "shipped")
            st.success(f"{len(sel_ids)} 件を出荷済みにしました。")
            for m in msgs:
                st.write(m)
            if komeful_flag:
                st.link_button("コメフルの出荷処理を開く", komeful.SELLER_URL, use_container_width=True)
            st.rerun(scope="fragment")

    # ---- ステップ5：集荷を依頼する ----
    ui.step(5, "集荷を依頼する",
            "送り状を貼ったら、ヤマトに集荷を依頼します。日時を選ぶだけ。個数は自動で入ります")

    p1, p2, p3 = st.columns([2, 2, 1])
    # 当日も選べる。実際に当日が可能かは時間帯の締切しだい（ヤマト側で判定）
    pdate = p1.date_input("集荷希望日", value=today(), min_value=today(),
                          max_value=today() + timedelta(days=7), key="pickup_date")
    ptime = p2.selectbox("集荷時間帯", list(PICKUP_TIMES), key="pickup_time")
    default_cnt = int(db.get_setting("last_shipped_count") or 1)
    pcnt = p3.number_input("個数", min_value=1, value=max(1, default_cnt), step=1,
                           key="pickup_count")
    if pdate == today():
        st.caption("※当日の集荷は地域ごとの受付締切時刻まで。締切を過ぎていると当日は選べず、"
                   "その場合は『翌日以降を選んでください』と表示されます（ヤマトの締切に従います）。")

    if st.button("集荷を依頼する", type="primary", use_container_width=True,
                 help="PCの常駐プログラムがヤマトに自動ログインして集荷依頼を送ります（PCが起動している必要があります）"):
        _start_agent("b2_pickup_request", "b2_pickup_progress", "b2_pickup_result",
                     "集荷依頼", payload_key="b2_pickup_payload", payload={
                         "date": pdate.strftime("%Y/%m/%d"), "time": ptime,
                         "count": int(pcnt), "dry_run": False, "explore": False,
                     })

    pres = db.get_setting("b2_pickup_result")
    if pres and not pres.get("pending"):
        icon = "✓" if pres.get("ok") else "⚠"
        st.caption(f'{icon} 前回の集荷依頼（{pres.get("at","")}）：{pres.get("summary","")}')

    with st.expander("初回の調整（集荷ページの構造を調べる）"):
        st.caption("ヤマトの集荷依頼ページは初回だけ構造の確認が必要です。下のボタンで調査用の情報を保存します（依頼は送りません）。")
        if st.button("集荷ページを調べる（送信しない）", use_container_width=True):
            _start_agent("b2_pickup_request", "b2_pickup_progress", "b2_pickup_result",
                         "集荷ページの調査", payload_key="b2_pickup_payload",
                         payload={"explore": True})


# ===========================================================================
# 📋 注文（一覧・検索・編集）
# ===========================================================================
@st.fragment
def view_orders():
    top1, top2 = st.columns([4, 1.3])
    q = top1.text_input("検索", placeholder="🔍 お客様名・商品名で検索",
                        key="oq", label_visibility="collapsed")
    if top2.button("＋ 注文追加", use_container_width=True, type="primary"):
        dlg_add_order()
    flt = st.segmented_control(
        "状態", ["未出荷", "出荷済み", "すべて"], default="未出荷",
        key="oflt", label_visibility="collapsed",
    ) or "未出荷"

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
        # 削除確認中はこの行を確認表示に置き換える
        if st.session_state.get("del_order") == o["id"]:
            st.markdown(
                f'<div class="o-card" style="border-color:#E07A7A66">'
                f'<span class="o-name">{o["customer_name"]} 様</span> の注文'
                f'（{o["product_name"]} ×{o["qty"] or 1}）を削除しますか？</div>',
                unsafe_allow_html=True)
            d1, d2 = st.columns(2)
            if d1.button("🗑 削除する", key=f'dy{o["id"]}', type="primary",
                         use_container_width=True):
                db.delete_order(o["id"])
                st.session_state.pop("del_order", None)
                st.rerun(scope="fragment")
            if d2.button("やめる", key=f'dn{o["id"]}', use_container_width=True):
                st.session_state.pop("del_order", None)
                st.rerun(scope="fragment")
            continue

        c1, c2 = st.columns([6, 1])
        meta = f'<div class="o-meta">注文日 {o["order_date"] or "-"}／出荷予定 {o["ship_date"] or "-"}</div>'
        c1.markdown(ui.order_card(o, meta), unsafe_allow_html=True)
        with c2.popover("操作", use_container_width=True):
            if st.button("✎ 編集", key=f'oe{o["id"]}', use_container_width=True):
                dlg_edit_order(o)
            if o["status"] != "shipped":
                if st.button("✓ 出荷済みにする", key=f'os{o["id"]}', use_container_width=True):
                    db.update_order_status([o["id"]], "shipped")
                    st.rerun(scope="fragment")
            else:
                if st.button("↩ 未出荷に戻す", key=f'ob{o["id"]}', use_container_width=True):
                    db.update_order_status([o["id"]], "pending")
                    st.rerun(scope="fragment")
            if st.button("🗑 削除", key=f'od{o["id"]}', use_container_width=True):
                st.session_state["del_order"] = o["id"]
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
    if c2.button("＋ 新規追加", use_container_width=True):
        dlg_customer(None)

    with st.expander("ヤマトから過去の発送を取り込む（顧客マスタを自動更新）"):
        st.caption("B2クラウドの発行済データを取得して、お届け先を顧客マスタに登録し、過去注文として記録します。"
                   "同じ伝票番号は重複しません。売上・分析の元データになります。")
        if st.button("過去の発送をまとめて取り込む", type="primary", use_container_width=True,
                     help="PCの常駐プログラムがB2クラウドに自動ログインして発行済データを取得します（PC起動が必要・ヤマト利用時間 7:00〜25:00）"):
            _start_agent("b2_history_request", "b2_history_progress", "b2_history_result",
                         "過去の発送の取り込み")
        hres = db.get_setting("b2_history_result")
        if hres and not hres.get("pending"):
            icon = "✓" if hres.get("ok") else "⚠"
            st.caption(f'{icon} 前回（{hres.get("at","")}）：{hres.get("summary","")}')

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
        n = order_counts.get(c["id"], 0)
        if st.session_state.get("del_cust") == c["id"]:
            st.markdown(
                f'<div class="o-card" style="border-color:#E07A7A66">'
                f'<span class="o-name">{c["name"]} 様</span> を削除しますか？</div>',
                unsafe_allow_html=True)
            d1, d2 = st.columns(2)
            if d1.button("🗑 削除する", key=f'cdy{c["id"]}', type="primary",
                         use_container_width=True):
                db.delete_customer(c["id"])
                st.session_state.pop("del_cust", None)
                st.rerun(scope="fragment")
            if d2.button("やめる", key=f'cdn{c["id"]}', use_container_width=True):
                st.session_state.pop("del_cust", None)
                st.rerun(scope="fragment")
            continue
        k1, k2 = st.columns([6, 1])
        k1.markdown(
            f'<div class="o-card"><span class="o-name">{c["name"]} 様</span>　'
            f'<span class="o-meta">注文 {n} 回</span>'
            f'<div class="o-meta">〒{c["zip"]}　{c["address"]}{c["address2"] or ""}　☎ {c["tel"]}</div></div>',
            unsafe_allow_html=True,
        )
        with k2.popover("操作", use_container_width=True):
            if st.button("✎ 編集", key=f'ce{c["id"]}', use_container_width=True):
                dlg_customer(c)
            if n == 0:
                if st.button("🗑 削除", key=f'cd{c["id"]}', use_container_width=True):
                    st.session_state["del_cust"] = c["id"]
                    st.rerun(scope="fragment")
            else:
                st.caption("注文履歴があるため削除不可")


# ===========================================================================
# ⚙ 設定
# ===========================================================================
def view_settings():
    tab_sender, tab_prod, tab_base, tab_print, tab_data = st.tabs(
        ["送り主", "商品", "BASE連携", "印刷", "データ"]
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
            st.markdown("**請求先（ヤマト発払いの運賃請求先）**")
            st.caption("送り状の発行に必須です。ヤマトの『お客さまコード』と『運賃管理番号』を入れます。")
            c7, c8 = st.columns(2)
            ccode = c7.text_input("ご請求先顧客コード", s.get("customer_code", ""),
                                  help="ヤマトのお客さまコード（例：08060303705）")
            sno = c8.text_input("運賃管理番号", s.get("shipping_no", ""),
                                help="通常は 01")
            if st.form_submit_button("保存", type="primary"):
                db.set_setting("sender", {
                    "name": name, "kana": kana, "tel": tel,
                    "zip": zipc, "address": addr, "address2": addr2,
                    "customer_code": ccode.strip(), "shipping_no": sno.strip(),
                })
                st.success("保存しました。")

    with tab_prod:
        st.caption("「精米が必要」の商品だけが精米量に加算されます。『品名(送り状用)』が送り状に印字されます。"
                   "『単価』は売上ダッシュボード・顧客分析に使います（売上＝単価×個数）。")
        products = db.list_products(active_only=False)
        pdf = pd.DataFrame([{
            "商品名": p["name"], "区分": p["category"], "重量kg": p["weight_kg"],
            "単価(円)": int(p.get("price") or 0),
            "精米が必要": bool(p["needs_milling"]), "品名(送り状用)": p["yamato_name"],
            "並び順": p["sort_order"], "有効": bool(p["active"]),
        } for p in products])
        edited = st.data_editor(
            pdf, use_container_width=True, hide_index=True, num_rows="dynamic",
            column_config={
                "区分": st.column_config.SelectboxColumn("区分", options=["精米", "玄米", "複合", "その他"]),
                "単価(円)": st.column_config.NumberColumn("単価(円)", min_value=0, step=100, format="%d"),
                "精米が必要": st.column_config.CheckboxColumn("精米が必要"),
                "有効": st.column_config.CheckboxColumn("有効"),
            },
            key="prod_editor",
        )
        if st.button("商品を保存", type="primary"):
            for _, r in edited.iterrows():
                if not str(r["商品名"]).strip():
                    continue
                db.upsert_product({
                    "name": r["商品名"], "category": r["区分"] or "その他",
                    "weight_kg": float(r["重量kg"] or 0),
                    "price": float(r["単価(円)"] or 0),
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
            st.success("BASE連携は設定済みです（自動取込・自動出荷が使えます）")
        with st.form("base_form"):
            client_id = st.text_input("Client ID", cfg.get("client_id", ""))
            client_secret = st.text_input("Client Secret", cfg.get("client_secret", ""), type="password")
            redirect_uri = st.text_input("Redirect URI", cfg.get("redirect_uri", ""))
            refresh_token = st.text_input("リフレッシュトークン", cfg.get("refresh_token", ""), type="password")
            if st.form_submit_button("保存", type="primary"):
                db.set_setting("base_config", {
                    "client_id": client_id, "client_secret": client_secret,
                    "redirect_uri": redirect_uri, "refresh_token": refresh_token,
                })
                st.success("保存しました。")

        st.divider()
        ui.section("発送通知メッセージ", "出荷確定でBASEからお客様へ送る発送メールに添える文面です")
        st.caption("文中の ○○ は自動でお客様のお名前に置き換わります。")
        with st.form("dispatch_msg_form"):
            msg = st.text_area(
                "発送通知の文面", db.get_setting("dispatch_message") or DEFAULT_DISPATCH_MESSAGE,
                height=240,
            )
            if st.form_submit_button("保存", type="primary"):
                db.set_setting("dispatch_message", msg)
                st.success("保存しました。")

    with tab_print:
        from lib import mailer
        ui.section("クラウド印刷（PC不要）",
                   "送り状をクラウドで発行し、PDFをプリンタへメール送信して印刷します")
        st.caption("EP-810A など Epson Connect『メールプリント』対応機が必要です。"
                   "下の項目を入れて保存するだけ（Streamlit Cloudの設定は不要）。")

        mc = db.get_setting("mail_config") or {}
        with st.form("mail_form"):
            pe = st.text_input("① プリンタのメールプリント宛先",
                               value=db.get_setting("print_email") or "",
                               placeholder="xxxxxxxx@print.epsonconnect.com")
            muser = st.text_input("② 送信元メールアドレス（Gmail）",
                                  value=mc.get("user", ""), placeholder="abekeyn@gmail.com")
            mpass = st.text_input("③ アプリパスワード（Gmailで発行した16桁）",
                                  value=mc.get("password", ""), type="password",
                                  help="Googleアカウント→セキュリティ→アプリパスワードで作成")
            with st.expander("詳細（通常は変更不要）"):
                mhost = st.text_input("SMTPサーバー", value=mc.get("host", "smtp.gmail.com"))
                mport = st.text_input("ポート", value=str(mc.get("port", "587")))
                mfrom = st.text_input("差出人（空なら送信元と同じ）", value=mc.get("from", ""))
            if st.form_submit_button("保存", type="primary"):
                db.set_setting("print_email", pe.strip())
                db.set_setting("mail_config", {
                    "host": (mhost or "smtp.gmail.com").strip(),
                    "port": (mport or "587").strip(),
                    "user": muser.strip(), "password": mpass.strip(),
                    "from": (mfrom or muser).strip(),
                })
                st.success("保存しました。")

        ok, why = mailer.is_configured()
        if ok:
            st.success("✅ メール印刷の設定はそろっています。")
        else:
            st.warning(f"あと少し：{why}")

        if st.button("メール設定をテスト（自分宛に送信）"):
            tok, tmsg = mailer.send_test()
            (st.success if tok else st.error)(tmsg)

        with st.expander("Gmailアプリパスワードの作り方"):
            st.markdown(
                "1. **2段階認証プロセスをオン**にする（Googleアカウント→セキュリティ）\n"
                "2. **https://myaccount.google.com/apppasswords** を開く\n"
                "3. 名前（例：kome-print）を入れて**作成** → 表示された**16桁**をコピー\n"
                "4. 上の③に貼り付けて保存\n\n"
                "※プリンタ側（Epson Connect）の『受信を許可するアドレス』に、"
                "この②のGmailを必ず追加してください。"
            )

    with tab_data:
        ui.section("顧客データの取込", "ヤマトB2クラウドの発行済データCSVから顧客を登録します")
        up = st.file_uploader("発行済データCSV", type=["csv"], key="reimport")
        hist = st.checkbox("過去の注文も記録する（出荷済み扱い）", value=False)
        if up is not None and st.button("取り込む（顧客データ）"):
            r = seed.import_issued_csv(up.getvalue(), import_history=hist)
            db.clear_cache()
            st.success(f"顧客 +{r['customers']} 名／注文 +{r['orders']} 件")

        st.divider()
        ui.section("ログアウト")
        if st.button("ログアウト"):
            st.session_state.pop("authed", None)
            st.rerun()

        st.divider()
        ui.section("全データの初期化", "注文・顧客・設定をすべて消去します（元に戻せません）")
        confirm = st.text_input('「リセット」と入力すると実行できます', "")
        if st.button("全データをリセット", disabled=(confirm != "リセット")):
            db.reset_all()
            st.success("初期化しました。再読み込みすると初期状態になります。")


# ===========================================================================
# 📊 分析（売上ダッシュボード・顧客分析）
# ===========================================================================
def _man(v: float) -> str:
    """金額を万単位の短い表記に（1万円以上は『16.8万』、未満はカンマ区切り）。"""
    return f"{v / 10000:.1f}万" if v >= 10000 else f"{v:,.0f}"


def _gold_gradient(horizontal: bool = False):
    """金色の線形グラデーション（縦棒は上明→下暗、横棒は左明→右暗）。"""
    coords = dict(x1=0, x2=1, y1=0, y2=0) if horizontal else dict(x1=0, x2=0, y1=0, y2=1)
    return alt.Gradient(
        gradient="linear",
        stops=[alt.GradientStop(color="#EBCC72", offset=0.0),
               alt.GradientStop(color="#B5862B", offset=1.0)],
        **coords,
    )


_AX_LBL = "#CFCBDD"
_AX_LINE = "#3A3D63"


def _sales_bar_chart(mrows):
    """月別売上の縦棒グラフ（金グラデ・角丸・値ラベル付き）。"""
    df = pd.DataFrame([{"年月": m["年月"], "売上": float(m["売上"]),
                        "件数": m["件数"], "表示": _man(m["売上"])} for m in mrows])
    maxv = max([1.0, *df["売上"]])
    base = alt.Chart(df)
    bars = base.mark_bar(cornerRadiusTopLeft=7, cornerRadiusTopRight=7,
                         color=_gold_gradient()).encode(
        x=alt.X("年月:N", sort=None,
                scale=alt.Scale(paddingInner=0.45, paddingOuter=0.25),
                axis=alt.Axis(title=None, labelAngle=0, labelColor=_AX_LBL,
                              domainColor=_AX_LINE, tickColor=_AX_LINE,
                              labelFontSize=12, labelPadding=8)),
        y=alt.Y("売上:Q", axis=None, scale=alt.Scale(domain=[0, maxv * 1.18])),
        tooltip=[alt.Tooltip("年月:N", title="年月"),
                 alt.Tooltip("売上:Q", title="売上", format=",.0f"),
                 alt.Tooltip("件数:Q", title="件数")],
    )
    labels = base.mark_text(dy=-9, color="#F2EDE0", fontSize=12,
                            fontWeight="bold").encode(
        x=alt.X("年月:N", sort=None), y="売上:Q", text="表示:N")
    return ((bars + labels).properties(height=300)
            .configure_view(stroke=None)
            .configure(background="rgba(0,0,0,0)")
            .configure_axis(grid=False))


def _product_bar_chart(psales):
    """商品別売上の横棒グラフ（売上の多い順・値ラベル付き）。"""
    df = pd.DataFrame([{"商品": p["商品"], "売上": float(p["売上"]),
                        "個数": p["個数"], "表示": _man(p["売上"])} for p in psales])
    maxv = max([1.0, *df["売上"]])
    base = alt.Chart(df)
    bars = base.mark_bar(cornerRadiusTopRight=6, cornerRadiusBottomRight=6,
                         color=_gold_gradient(horizontal=True)).encode(
        y=alt.Y("商品:N", sort="-x",
                axis=alt.Axis(title=None, labelColor="#EAE5D6", labelFontSize=12,
                              domainColor=_AX_LINE, tickColor=_AX_LINE, labelLimit=180)),
        x=alt.X("売上:Q", axis=None, scale=alt.Scale(domain=[0, maxv * 1.18])),
        tooltip=[alt.Tooltip("商品:N", title="商品"),
                 alt.Tooltip("売上:Q", title="売上", format=",.0f"),
                 alt.Tooltip("個数:Q", title="個数")],
    )
    labels = base.mark_text(align="left", dx=6, color="#F2EDE0", fontSize=12,
                            fontWeight="bold").encode(
        y=alt.Y("商品:N", sort="-x"), x="売上:Q", text="表示:N")
    return ((bars + labels).properties(height=max(130, len(df) * 44))
            .configure_view(stroke=None)
            .configure(background="rgba(0,0,0,0)")
            .configure_axis(grid=False))


def _segment_bar_chart(summary):
    """属性別の人数を、属性ごとの色で横棒表示する。"""
    df = pd.DataFrame([{"属性": s["属性"], "人数": s["人数"]} for s in summary])
    domain = [s["属性"] for s in summary]
    rng = [s["色"] for s in summary]
    maxv = max([1, *df["人数"]])
    base = alt.Chart(df)
    bars = base.mark_bar(cornerRadiusTopRight=6, cornerRadiusBottomRight=6).encode(
        y=alt.Y("属性:N", sort=domain, scale=alt.Scale(paddingInner=0.4),
                axis=alt.Axis(title=None, labelColor="#EAE5D6", labelFontSize=13,
                              domainColor=_AX_LINE, tickColor=_AX_LINE)),
        x=alt.X("人数:Q", axis=None, scale=alt.Scale(domain=[0, maxv * 1.2])),
        color=alt.Color("属性:N", scale=alt.Scale(domain=domain, range=rng), legend=None),
        tooltip=[alt.Tooltip("属性:N", title="属性"), alt.Tooltip("人数:Q", title="人数")],
    )
    labels = base.mark_text(align="left", dx=6, color="#F2EDE0", fontSize=12,
                            fontWeight="bold").encode(
        y=alt.Y("属性:N", sort=domain), x="人数:Q", text=alt.Text("人数:Q", format="d"))
    return ((bars + labels).properties(height=max(150, len(df) * 48))
            .configure_view(stroke=None)
            .configure(background="rgba(0,0,0,0)")
            .configure_axis(grid=False))


def _top_customers_chart(stats, n: int = 8):
    """累計購入額の多い顧客 上位nを横棒で表示する。"""
    rows = sorted(stats, key=lambda x: x["累計金額"], reverse=True)[:n]
    df = pd.DataFrame([{"顧客": r["顧客"], "累計金額": float(r["累計金額"]),
                        "回数": r["回数"], "表示": _man(r["累計金額"])} for r in rows])
    maxv = max([1.0, *df["累計金額"]])
    base = alt.Chart(df)
    bars = base.mark_bar(cornerRadiusTopRight=6, cornerRadiusBottomRight=6,
                         color=_gold_gradient(horizontal=True)).encode(
        y=alt.Y("顧客:N", sort="-x", scale=alt.Scale(paddingInner=0.35),
                axis=alt.Axis(title=None, labelColor="#EAE5D6", labelFontSize=12,
                              domainColor=_AX_LINE, tickColor=_AX_LINE, labelLimit=160)),
        x=alt.X("累計金額:Q", axis=None, scale=alt.Scale(domain=[0, maxv * 1.18])),
        tooltip=[alt.Tooltip("顧客:N", title="顧客"),
                 alt.Tooltip("累計金額:Q", title="累計金額", format=",.0f"),
                 alt.Tooltip("回数:Q", title="回数")],
    )
    labels = base.mark_text(align="left", dx=6, color="#F2EDE0", fontSize=12,
                            fontWeight="bold").encode(
        y=alt.Y("顧客:N", sort="-x"), x="累計金額:Q", text="表示:N")
    return ((bars + labels).properties(height=max(130, len(df) * 40))
            .configure_view(stroke=None)
            .configure(background="rgba(0,0,0,0)")
            .configure_axis(grid=False))


def view_analytics():
    tab_sales, tab_cust = st.tabs(["売上", "顧客"])
    orders = db.list_orders()

    with tab_sales:
        if not any(p.get("price") for p in db.list_products(active_only=False)):
            st.info("売上金額を出すには、設定 → 商品 で各商品の『単価(円)』を入力してください。")
        months = analytics.monthly_sales(orders)
        years = analytics.yearly_sales(orders)
        if not months:
            st.caption("まだ集計できる注文がありません。")
        else:
            this_year = today().year
            ty = next((y["売上"] for y in years if y["年"] == this_year), 0)
            ly = next((y["売上"] for y in years if y["年"] == this_year - 1), 0)
            total = sum(m["売上"] for m in months)
            ocount = sum(m["件数"] for m in months)
            avg = total / ocount if ocount else 0

            # 前年比
            if ly:
                rate = (ty - ly) / ly * 100
                cls = "up" if rate >= 0 else "down"
                yoy = f'前年比 <span class="{cls}">{rate:+.0f}%</span>'
            else:
                yoy = "前年データなし"

            k1, k2, k3 = st.columns(3)
            k1.markdown(ui.kpi(f"{this_year}年の売上", f"{ty:,.0f}", yoy, yen=True),
                        unsafe_allow_html=True)
            k2.markdown(ui.kpi("全期間の売上", f"{total:,.0f}", f"累計 {ocount:,} 件", yen=True),
                        unsafe_allow_html=True)
            k3.markdown(ui.kpi("平均購入額", f"{avg:,.0f}", "1注文あたり", yen=True),
                        unsafe_allow_html=True)

            st.write("")
            # 年の絞り込み
            yopts = ["すべて"] + [str(y["年"]) for y in years]
            ysel = st.selectbox("対象期間", yopts, key="an_year")
            mrows = sorted(
                months if ysel == "すべて" else [m for m in months if m["年月"].startswith(ysel)],
                key=lambda x: x["年月"],
            )

            ui.section("月別の売上")
            st.altair_chart(_sales_bar_chart(mrows), use_container_width=True)

            with st.expander("月別の明細を表で見る"):
                tbl = pd.DataFrame(mrows)
                tbl["売上"] = tbl["売上"].map(lambda v: f"¥{v:,.0f}")
                tbl["精米kg"] = tbl["精米kg"].map(lambda v: f"{v:g}")
                st.dataframe(tbl, use_container_width=True, hide_index=True)

            ui.section("商品別の売上")
            psales = analytics.product_sales(orders if ysel == "すべて"
                                              else [o for o in orders
                                                    if (d := analytics.order_date(o)) and d.year == int(ysel)])
            if psales:
                st.altair_chart(_product_bar_chart(psales), use_container_width=True)

    with tab_cust:
        st.caption("過去の購入実績から顧客の属性を判定し、それぞれに打つべき『次の一手』を提案します。")
        stats = analytics.customer_stats(orders)
        if not stats:
            st.caption("まだ分析できる注文がありません。")
        else:
            summary = analytics.segment_summary(stats)
            total = len(stats)
            repeat = sum(1 for s in stats if s["回数"] >= 2)
            followup = sum(1 for s in stats if s["属性"] in ("離脱注意", "休眠客"))

            k1, k2, k3 = st.columns(3)
            k1.markdown(ui.kpi("顧客数", f"{total:,}", "登録のべ人数"),
                        unsafe_allow_html=True)
            k2.markdown(ui.kpi("リピーター率", f"{repeat / total * 100:.0f}%",
                               f"2回以上 {repeat:,} 名"), unsafe_allow_html=True)
            k3.markdown(ui.kpi("要フォロー", f"{followup:,}", "離脱注意＋休眠"),
                        unsafe_allow_html=True)

            st.write("")
            ui.section("顧客の構成", "属性ごとの人数")
            st.altair_chart(_segment_bar_chart(summary), use_container_width=True)

            ui.section("属性ごとの『次の一手』")
            for s in summary:
                st.markdown(
                    f'<div class="o-card" style="border-left:4px solid {s["色"]}">'
                    f'<span class="o-name" style="color:{s["色"]}">{s["属性"]}</span>'
                    f'　<span class="o-meta">{s["人数"]} 名</span>'
                    f'<div class="o-line">{s["次の一手"]}</div></div>',
                    unsafe_allow_html=True,
                )

            if any(s["累計金額"] for s in stats):
                ui.section("お得意様 上位", "累計購入額の多い順")
                st.altair_chart(_top_customers_chart(stats), use_container_width=True)

            ui.section("顧客一覧")
            segs = ["すべて"] + [s["属性"] for s in summary]
            seg_sel = st.segmented_control("属性で絞り込み", segs, default="すべて",
                                           key="an_seg") or "すべて"
            rows = stats if seg_sel == "すべて" else [s for s in stats if s["属性"] == seg_sel]
            df = pd.DataFrame(rows)
            df["累計金額"] = df["累計金額"].map(lambda v: f"¥{v:,.0f}")
            st.dataframe(df, use_container_width=True, hide_index=True)


# ===========================================================================
# ルーティング
# ===========================================================================
view = ui.render_nav()
_auto_sync_history()
_agent_progress()
if view == "ホーム":
    view_home()
elif view == "注文":
    view_orders()
elif view == "顧客":
    view_customers()
elif view == "分析":
    view_analytics()
else:
    view_settings()
