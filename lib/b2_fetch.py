# -*- coding: utf-8 -*-
"""ヤマトB2クラウドから発行済データを自動取得する（ブラウザ自動操作）。

PCの常駐エージェントから実行する。流れ：
1. ヤマトビジネスメンバーズに自動ログイン
2. B2クラウドを開く
3. 発行済データを検索 → CSVダウンロード
4. 照合 → 伝票番号記録 → 出荷完了 → BASE反映（lib/shipping.py）

認証情報は .streamlit/secrets.toml（PC内のみ・Git除外）：
    YAMATO_CUSTOMER_CODE = "お客さまコード"
    YAMATO_ID = "ログインID（メールアドレス等）"
    YAMATO_PASSWORD = "パスワード"

※ 画面構成が変わると動かなくなることがある。失敗時は b2_debug/ に
   スクリーンショットを保存するので、それを見て直す。
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from . import config, shipping

ROOT = Path(__file__).resolve().parent.parent
DEBUG_DIR = ROOT / "b2_debug"

YBM_URL = "https://bmypage.kuronekoyamato.co.jp/"


class B2Error(Exception):
    pass


def _launch_chromium(pl, headful: bool):
    """Chromiumを起動。ブラウザ実体が消えていたら自動で入れ直して再試行する
    （Windowsのディスク掃除でキャッシュが消えても自己修復する）。"""
    import subprocess
    import sys as _sys
    try:
        return pl.chromium.launch(headless=not headful)
    except Exception as e:  # noqa: BLE001
        if "Executable doesn't exist" not in str(e) and "playwright install" not in str(e):
            raise
        subprocess.run(
            [_sys.executable, "-m", "playwright", "install", "chromium", "chromium-headless-shell"],
            timeout=600, check=False,
        )
        return pl.chromium.launch(headless=not headful)


def _shot(page, name: str) -> str:
    """デバッグ用スクリーンショットを保存してパスを返す。"""
    try:
        DEBUG_DIR.mkdir(exist_ok=True)
        p = DEBUG_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name}.png"
        page.screenshot(path=str(p), full_page=True)
        return str(p)
    except Exception:  # noqa: BLE001
        return ""


def _first_visible(page, selectors: list[str], timeout_ms: int = 8000):
    """候補セレクタのうち最初に見つかった要素を返す。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return loc
            except Exception:  # noqa: BLE001
                continue
        time.sleep(0.4)
    return None


def _login_and_open_b2(ctx, page, code: str, pw: str):
    """ヤマトビジネスメンバーズにログインし、B2クラウド本体のページを返す。"""
    import re as _re

    # ---- ログイン ----
    page.goto(YBM_URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    login_btn = _first_visible(page, [
        'text="ログイン"', 'a:has-text("ログイン")', 'button:has-text("ログイン")',
    ], 4000)
    if login_btn and _first_visible(page, ['input[type="password"]'], 1500) is None:
        login_btn.click()
        page.wait_for_timeout(2500)

    pw_input = _first_visible(page, ['input[type="password"]'], 12000)
    if pw_input is None:
        raise B2Error("ログイン画面が見つかりません: " + _shot(page, "login_not_found"))
    texts = page.locator(
        'input[type="text"]:visible, input[type="tel"]:visible, input:not([type]):visible'
    )
    if texts.count() < 1:
        raise B2Error("ログイン入力欄が見つかりません: " + _shot(page, "login_inputs"))
    texts.nth(0).fill(code)
    pw_input.fill(pw)

    submit = _first_visible(page, ['input[type="submit"]', 'button[type="submit"]'], 2500)
    if submit is None:
        exact = _re.compile(r"^\s*ログイン\s*$")
        cand = page.get_by_role("button", name=exact)
        if cand.count() == 0:
            cand = page.get_by_role("link", name=exact)
        submit = cand.first if cand.count() > 0 else None
    if submit is not None:
        submit.click()
    else:
        pw_input.press("Enter")

    for _ in range(40):
        page.wait_for_timeout(500)
        try:
            if not page.locator('input[type="password"]').first.is_visible():
                break
        except Exception:  # noqa: BLE001
            break
    else:
        raise B2Error("ログインに失敗した可能性があります（コード/パスワードを確認）: " + _shot(page, "login_failed"))
    page.wait_for_timeout(3000)

    # ---- B2クラウドを開く ----
    b2_link = _first_visible(page, [
        'a:has-text("B2クラウド")', 'text="送り状発行システムB2クラウド"',
        'a:has-text("送り状発行")', 'img[alt*="B2"]',
    ], 12000)
    if b2_link is None:
        raise B2Error("B2クラウドへのリンクが見つかりません: " + _shot(page, "b2_link"))
    b2 = page
    try:
        with ctx.expect_page(timeout=8000) as pinfo:
            b2_link.click()
        b2 = pinfo.value
    except Exception:  # noqa: BLE001
        pass
    b2.wait_for_load_state("domcontentloaded")
    b2.wait_for_timeout(4000)

    use_btn = _first_visible(b2, [
        'a:has-text("このサービスを利用する")', 'button:has-text("このサービスを利用する")',
    ], 4000)
    if use_btn is not None:
        try:
            with ctx.expect_page(timeout=10000) as pinfo2:
                use_btn.click()
            b2 = pinfo2.value
        except Exception:  # noqa: BLE001
            pass
        b2.wait_for_load_state("domcontentloaded")
        b2.wait_for_timeout(6000)
    return b2


def issue_and_print(csv_bytes: bytes, pattern: str | None = None,
                    headful: bool = False, dry_run: bool = False,
                    explore: bool = False) -> dict:
    """送り状CSVをB2クラウドの「外部データから発行」に通して送り状を発行する。

    手順：① データ取込み（パターン選択・ファイル選択・取込み開始）
         ② 取込み結果表示  ← dry_run はここで停止（発行しない＝安全）
         ③ 印刷内容の確認 → ④ 登録完了・印刷（PDFを取得）

    returns {"issued": bool, "pdf": bytes|None, "rows": int, "message": str}
    dry_run=True のときは取込み結果だけ確認し、発行・PDF取得はしない。
    """
    import re as _re
    import tempfile

    code = config.get_secret("YAMATO_CUSTOMER_CODE", "")
    pw = config.get_secret("YAMATO_PASSWORD", "")
    if not (code and pw):
        raise B2Error("ヤマトのログイン情報が未設定です（secrets.toml）")
    pattern = pattern or config.get_secret("B2_IMPORT_PATTERN", "") or "基本レイアウト(csv,xls,xlsx)"

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pl:
        browser = _launch_chromium(pl, headful)
        ctx = browser.new_context(locale="ja-JP", accept_downloads=True,
                                  viewport={"width": 1366, "height": 1000})
        page = ctx.new_page()
        try:
            b2 = _login_and_open_b2(ctx, page, code, pw)

            # 「外部データから発行」のメニューカードを開く
            # （部分一致・DOM順で最初＝メニューカード。FAQの長文リンクは後方にある）
            import re as _re2
            link = b2.get_by_text(_re2.compile("外部データから発行")).first
            if link.count() == 0:
                raise B2Error("「外部データから発行」が見つかりません: " + _shot(b2, "issue_menu"))
            try:
                link.click(timeout=8000)
            except Exception:  # noqa: BLE001
                link.click(force=True)
            b2.wait_for_timeout(5000)

            # 取込みパターンを選択
            try:
                sel = b2.locator("select").first
                sel.select_option(label=pattern, timeout=4000)
            except Exception:  # noqa: BLE001  ラベル不一致時は部分一致で
                try:
                    opts = b2.locator("select").first.locator("option").all()
                    for o in opts:
                        if pattern.split("(")[0] in (o.inner_text() or ""):
                            b2.locator("select").first.select_option(value=o.get_attribute("value"))
                            break
                except Exception:  # noqa: BLE001
                    _shot(b2, "pattern_select")
            b2.wait_for_timeout(1500)

            # ファイルをアップロード（全 input[type=file] に投入し、効いたものを採用）
            tmp = Path(tempfile.gettempdir()) / "abe_yamato_issue.csv"
            tmp.write_bytes(csv_bytes)
            _shot(b2, "issue_before_upload")
            file_inputs = []
            for fr in b2.frames:
                try:
                    for i in range(fr.locator('input[type="file"]').count()):
                        file_inputs.append(fr.locator('input[type="file"]').nth(i))
                except Exception:  # noqa: BLE001
                    continue
            if not file_inputs:
                raise B2Error("ファイル選択欄が見つかりません: " + _shot(b2, "no_file_input"))
            for fin in file_inputs:
                try:
                    fin.set_input_files(str(tmp), timeout=8000)
                except Exception:  # noqa: BLE001
                    continue
            b2.wait_for_timeout(3500)
            _shot(b2, "after_upload")

            # 「取込み開始」をクリック（要素種別を問わずテキストで掴む）
            start = b2.get_by_text(_re.compile(r"取込み?開始")).last
            if start.count() == 0:
                raise B2Error("「取込み開始」が見つかりません: " + _shot(b2, "import_start"))
            try:
                start.click(timeout=8000)
            except Exception:  # noqa: BLE001
                start.click(force=True)
            b2.wait_for_timeout(6000)
            _shot(b2, "import_result")

            body = ""
            try:
                body = b2.inner_text("body")
            except Exception:  # noqa: BLE001
                pass
            m = _re.search(r"(\d+)\s*件", body)
            rows = int(m.group(1)) if m else 0
            if "エラー" in body and "0" in (m.group(1) if m else "0"):
                raise B2Error("取込みでエラーが出ました（パターン/列の対応を確認）: " + _shot(b2, "import_error"))

            if dry_run:
                return {"issued": False, "pdf": None, "rows": rows,
                        "message": f"[テスト] 取込み確認OK（{rows}件・発行はしていません）"}

            # 修正必要があれば中断（住所不備・運賃管理番号など）
            if _re.search(r"修正必要件数\s*([1-9]\d*)", body):
                raise B2Error("B2の取込みで修正が必要な行があります（住所・運賃管理番号・品名等）。"
                              "画面で『No.』の赤いセルを確認してください: " + _shot(b2, "need_fix"))

            # 全行を選択（ヘッダーのチェックボックス／効かなければ行ごと）
            try:
                b2.evaluate(
                    """() => {
                        const boxes=[...document.querySelectorAll('input[type=checkbox]')];
                        const all=boxes.find(b=>b.className.includes('allCheck'));
                        if(all && !all.checked) all.click();
                        if([...document.querySelectorAll('input[type=checkbox]:checked')].length<=1){
                            document.querySelectorAll('tbody tr input[type=checkbox]').forEach(b=>{if(!b.checked)b.click();});
                        }
                    }"""
                )
                b2.wait_for_timeout(1000)
            except Exception:  # noqa: BLE001
                pass

            # ② → ③「印刷内容の確認へ」
            conf = b2.get_by_text(_re.compile("印刷内容の確認")).last
            if conf.count() == 0:
                raise B2Error("「印刷内容の確認へ」が見つかりません: " + _shot(b2, "to_confirm"))
            try:
                conf.click(timeout=8000)
            except Exception:  # noqa: BLE001
                conf.click(force=True)
            b2.wait_for_timeout(5000)
            _shot(b2, "confirm_screen")

            if explore:
                return {"issued": False, "pdf": None, "rows": rows,
                        "message": f"[探索] 印刷内容の確認まで到達（{rows}件・発行はしていません）"}

            # ③ → ④ 発行（印刷）。PDFダウンロードを待つ
            holder = {}
            for pg in ctx.pages:
                pg.on("download", lambda d: holder.__setitem__("d", d))
            ctx.on("page", lambda pg: pg.on("download", lambda d: holder.__setitem__("d", d)))

            issue_btn = None
            for pat in [r"発行する", r"印刷する", r"^\s*発行\s*$", r"^\s*印刷\s*$", r"登録"]:
                loc = b2.get_by_text(_re.compile(pat)).last
                if loc.count() > 0:
                    issue_btn = loc
                    break
            if issue_btn is None:
                raise B2Error("発行ボタンが見つかりません: " + _shot(b2, "issue_btn"))
            try:
                issue_btn.click(timeout=8000)
            except Exception:  # noqa: BLE001
                issue_btn.click(force=True)
            b2.wait_for_timeout(4000)
            # 確認ダイアログ（OK/はい）
            okb = _first_visible(b2, ['button:has-text("OK")', 'button:has-text("はい")',
                                      'button:has-text("発行")'], 4000)
            if okb:
                try:
                    okb.click()
                except Exception:  # noqa: BLE001
                    pass

            for _ in range(40):
                if holder.get("d"):
                    break
                b2.wait_for_timeout(500)
            _shot(b2, "issue_done")
            if holder.get("d"):
                return {"issued": True, "pdf": Path(holder["d"].path()).read_bytes(),
                        "rows": rows, "message": f"発行しPDFを取得しました（{rows}件）"}
            return {"issued": True, "pdf": None, "rows": rows,
                    "message": f"発行しました（{rows}件）。PDFの自動取得はできませんでした（画面を確認）。"}
        finally:
            ctx.close()
            browser.close()


def fetch_and_process(days: int = 7, headful: bool = False, dry_run: bool = False) -> dict:
    """B2クラウドから発行済データを取得し、出荷確定まで実行する。

    dry_run=True なら照合結果の確認だけ行い、出荷確定・BASE反映はしない。
    returns {"rows", "shipped", "unmatched", "messages"} または raises B2Error
    """
    import re as _re

    code = config.get_secret("YAMATO_CUSTOMER_CODE", "")
    pw = config.get_secret("YAMATO_PASSWORD", "")
    if not (code and pw):
        raise B2Error("ヤマトのログイン情報が未設定です（secrets.toml に YAMATO_CUSTOMER_CODE / YAMATO_PASSWORD を設定）")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pl:
        browser = _launch_chromium(pl, headful)
        ctx = browser.new_context(
            locale="ja-JP",
            accept_downloads=True,
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        try:
            b2 = _login_and_open_b2(ctx, page, code, pw)

            # ---- 3. 発行済データの検索 ----
            hist = _first_visible(b2, [
                'a:has-text("発行済データ")', 'button:has-text("発行済データ")',
                'text="発行済データの検索"', 'a:has-text("検索・再印刷")',
                'a:has-text("送り状検索")',
            ], 15000)
            if hist is None:
                raise B2Error("「発行済データ」メニューが見つかりません: " + _shot(b2, "menu"))
            try:
                hist.click(timeout=8000)
            except Exception:  # noqa: BLE001  オーバーレイ等に遮られた場合
                hist.click(force=True)
            b2.wait_for_timeout(5000)

            # 検索期間を直近に絞る（出荷予定日の開始日を書き換え）
            try:
                from datetime import date as _date, timedelta as _td
                date_from = (_date.today() - _td(days=days)).strftime("%Y/%m/%d")
                for inp in b2.locator('input[type="text"]').all():
                    v = inp.input_value() or ""
                    if _re.match(r"\d{4}/\d{2}/\d{2}$", v.strip()):
                        inp.fill(date_from)
                        inp.press("Escape")  # カレンダー(datepicker)を閉じる
                        break
            except Exception:  # noqa: BLE001  期間はデフォルト(90日)のままでも動く
                pass
            # 開いたままのカレンダーが残っていれば閉じる
            try:
                b2.keyboard.press("Escape")
                b2.wait_for_timeout(600)
            except Exception:  # noqa: BLE001
                pass

            # 検索（「詳細検索オプションを開く」等を誤クリックしないよう厳密一致）
            exact_search = _re.compile(r"^\s*検索\s*$")
            search = b2.get_by_role("button", name=exact_search)
            if search.count() == 0:
                search = b2.get_by_role("link", name=exact_search)
            if search.count() == 0:
                search = b2.locator('input[type="button"][value="検索"], input[type="submit"][value="検索"]')
            if search.count() == 0:
                search = b2.get_by_text("検索", exact=True)
            if search.count() == 0:
                raise B2Error("検索ボタンが見つかりません: " + _shot(b2, "search_btn"))
            search.first.click()
            b2.wait_for_timeout(6000)

            # 検索結果が0件なら、ここで終了（出荷確定するものなし）
            checked = b2.evaluate(
                """() => {
                    const boxes = [...document.querySelectorAll('input[type=checkbox]')];
                    const all = boxes.find(b => b.className.includes('allCheck'));
                    if (all && !all.checked) { all.click(); }
                    let n = 0;
                    document.querySelectorAll('input[type=checkbox]').forEach(b => {
                        if (b.checked) n++;
                        // 全選択で連動しない場合に備え、行内のものを個別クリック
                    });
                    if (n <= 1) {
                        boxes.forEach(b => {
                            if (!b.className.includes('allCheck') && !b.checked) {
                                const tr = b.closest('tr');
                                if (tr && /\\d{4}-\\d{4}-\\d{4}/.test(tr.innerText||'')) b.click();
                            }
                        });
                    }
                    return [...document.querySelectorAll('input[type=checkbox]')]
                        .filter(b => b.checked && !b.className.includes('allCheck')).length;
                }"""
            )
            b2.wait_for_timeout(1000)
            if not checked:
                # 0件＝この期間に発行済データが無い
                return {"rows": 0, "shipped": 0, "unmatched": 0,
                        "messages": ["B2クラウドに対象期間の発行済データがありませんでした"]}

            # ---- 4. 「外部ファイルに出力」→ CSVダウンロード ----
            out_btn = _first_visible(b2, [
                'button:has-text("外部ファイルに出力")', 'a:has-text("外部ファイルに出力")',
                'input[value*="外部ファイル"]',
            ], 10000)
            if out_btn is None:
                raise B2Error("「外部ファイルに出力」が見つかりません: " + _shot(b2, "output_btn"))

            out_btn.click()
            b2.wait_for_timeout(2800)
            _shot(b2, "after_output_click")

            # ダイアログ「発行済データ外部出力」は別フレームの可能性 → 全フレームから探す
            def _frame_with(text: str):
                for fr in b2.frames:
                    try:
                        if fr.get_by_text(text, exact=False).count() > 0:
                            return fr
                    except Exception:  # noqa: BLE001
                        continue
                return None

            dlg = _frame_with("ファイル出力") or b2.main_frame

            # 「1行目に見出しを出力する」にチェック（解析に必須）
            try:
                dlg.evaluate(
                    """() => {
                        const lbl = [...document.querySelectorAll('*')].find(
                            e => (e.textContent||'').includes('1行目に見出し') && e.children.length===0);
                        let cb = null;
                        if (lbl) {
                            const row = lbl.closest('div,li,label,tr') || document;
                            cb = row.querySelector('input[type=checkbox]');
                        }
                        if (!cb) cb = document.querySelector('input[type=checkbox]');
                        if (cb && !cb.checked) cb.click();
                    }"""
                )
            except Exception:  # noqa: BLE001  見出しなしでも parse は耐性あり
                pass

            # どのページ/ポップアップで発火してもダウンロードを捕捉する
            holder = {}

            def _on_dl(d):
                holder["d"] = d

            for pg in ctx.pages:
                pg.on("download", _on_dl)
            ctx.on("page", lambda pg: pg.on("download", _on_dl))

            # 「ファイル出力」を native click（信頼イベントでダウンロードを発火させる）
            btn = dlg.get_by_role("button", name=_re.compile("ファイル出力"))
            if btn.count() == 0:
                btn = dlg.get_by_text(_re.compile(r"^\s*ファイル出力\s*$"))
            if btn.count() == 0:
                raise B2Error("「ファイル出力」ボタンが見つかりません: " + _shot(b2, "file_output"))
            btn.first.click()
            b2.wait_for_timeout(3500)  # 「ダウンロード準備が完了しました」ダイアログを待つ

            # 第2ダイアログ「ファイルダウンロード」の［ダウンロード］を押す
            def _click_download():
                dl_frame = _frame_with("ダウンロード準備") or _frame_with("ファイルダウンロード") or dlg
                loc = dl_frame.get_by_role("button", name=_re.compile(r"^\s*ダウンロード\s*$"))
                if loc.count() == 0:
                    loc = dl_frame.get_by_text(_re.compile(r"^\s*ダウンロード\s*$"))
                if loc.count() > 0:
                    loc.first.click()
                    return True
                return False

            for _ in range(10):
                if holder.get("d"):
                    break
                try:
                    _click_download()
                except Exception:  # noqa: BLE001
                    pass
                b2.wait_for_timeout(1500)
            if not holder.get("d"):
                raise B2Error("ファイル出力のダウンロードが発火しませんでした: " + _shot(b2, "no_download"))
            download = holder["d"]
            tmp = Path(download.path())
            raw = tmp.read_bytes()

            # ---- 5. 照合・出荷確定 ----
            if dry_run:
                from . import db, yamato
                rows = yamato.parse_issued_for_tracking(raw)
                unshipped = [o for o in db.list_orders() if o["status"] in ("pending", "milled")]
                matches, unmatched = shipping.match_tracking(rows, unshipped)
                return {
                    "rows": len(rows), "shipped": 0, "unmatched": len(unmatched),
                    "messages": [f'[テスト] 照合可能: {o["customer_name"]}様 → {r["tracking"]}'
                                 for r, o in matches],
                }
            result = shipping.process_issued_csv(raw)
            return result
        finally:
            ctx.close()
            browser.close()
