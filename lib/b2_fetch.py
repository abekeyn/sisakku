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


def _emit(progress, pct: int, step: str) -> None:
    """進捗コールバックを安全に呼ぶ（progressがNoneなら何もしない）。"""
    if progress:
        try:
            progress(pct, step)
        except Exception:  # noqa: BLE001  進捗通知の失敗で本処理を止めない
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


def _login_ybm(ctx, page, code: str, pw: str):
    """ヤマトビジネスメンバーズにログインする（ログイン後のページを返す）。"""
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
        # ヤマト側のメンテナンス／利用時間外（7:00〜25:00、B2クラウドは4:00〜）を判別
        try:
            body = page.inner_text("body", timeout=2000)
        except Exception:  # noqa: BLE001
            body = ""
        if "ご利用時間外" in body or "メンテナンス" in body:
            raise B2Error(
                "ヤマトは現在ご利用時間外です（ご利用可能：7:00〜25:00／B2クラウドは4:00〜）。"
                "時間内にもう一度お試しください。"
            )
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
    return page


def _login_and_open_b2(ctx, page, code: str, pw: str):
    """ヤマトビジネスメンバーズにログインし、B2クラウド本体のページを返す。"""
    page = _login_ybm(ctx, page, code, pw)

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
                    explore: bool = False, progress=None) -> dict:
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
            _emit(progress, 10, "ヤマトにログイン中")
            b2 = _login_and_open_b2(ctx, page, code, pw)
            _emit(progress, 30, "発行画面を開いています")

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
            _emit(progress, 45, "送り状データをアップロード中")
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
            _emit(progress, 60, "取り込み結果を確認中")
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

            _emit(progress, 80, "送り状を発行しています")
            # ③ → ④ 発行（印刷）。PDFダウンロードを待つ
            holder = {}
            for pg in ctx.pages:
                pg.on("download", lambda d: holder.__setitem__("d", d))
            ctx.on("page", lambda pg: pg.on("download", lambda d: holder.__setitem__("d", d)))

            issue_btn = None
            for pat in [r"発行開始", r"発行する", r"印刷開始", r"^\s*発行\s*$", r"登録"]:
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

            _emit(progress, 92, "PDFを取得しています")
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


def _download_issued(days: int = 7, headful: bool = False, progress=None) -> bytes | None:
    """B2クラウドにログインし、発行済データCSV(bytes)をダウンロードして返す。

    対象期間は「出荷予定日の開始日 = 今日 - days」に絞る。
    対象0件なら None を返す。失敗時は B2Error。
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
            _emit(progress, 15, "ヤマトにログイン中")
            b2 = _login_and_open_b2(ctx, page, code, pw)
            _emit(progress, 40, "発行済データを検索中")

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
                return None  # 0件＝この期間に発行済データが無い

            # ---- 4. 「外部ファイルに出力」→ CSVダウンロード ----
            _emit(progress, 65, "データをダウンロード中")
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
            return tmp.read_bytes()
        finally:
            ctx.close()
            browser.close()


def fetch_and_process(days: int = 7, headful: bool = False, dry_run: bool = False,
                      progress=None) -> dict:
    """B2クラウドから発行済データを取得し、出荷確定まで実行する。

    dry_run=True なら照合結果の確認だけ行い、出荷確定・BASE反映はしない。
    returns {"rows", "shipped", "unmatched", "messages"}
    """
    raw = _download_issued(days=days, headful=headful, progress=progress)
    if raw is None:
        return {"rows": 0, "shipped": 0, "unmatched": 0,
                "messages": ["B2クラウドに対象期間の発行済データがありませんでした"]}
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
    _emit(progress, 85, "伝票番号を照合して出荷確定中")
    return shipping.process_issued_csv(raw)


def fetch_history(days: int = 370, headful: bool = False, progress=None) -> dict:
    """B2クラウドから過去の発行済データを取得し、顧客マスタと過去注文を更新する。

    出荷確定やBASE反映はしない（あくまで履歴の取り込み）。
    同じ伝票番号の注文は重複登録しない。
    returns {"customers", "orders", "sender"}
    """
    from . import seed
    raw = _download_issued(days=days, headful=headful, progress=progress)
    if raw is None:
        return {"customers": 0, "orders": 0, "sender": False}
    _emit(progress, 85, "顧客マスタを更新中")
    return seed.import_issued_csv(raw, import_history=True)


def _dump_page(page, name: str) -> str:
    """ページのスクショ＋本文＋入力要素一覧を b2_debug に保存（本番前の構造調査用）。"""
    shot = _shot(page, name)
    try:
        DEBUG_DIR.mkdir(exist_ok=True)
        info = page.evaluate(
            """() => {
                const q = s => [...document.querySelectorAll(s)];
                const lab = el => (el.getAttribute('aria-label')||el.name||el.id||
                    el.placeholder||(el.value||'').slice(0,20)||'').trim();
                const sels = q('select').map(s => ({
                    label: lab(s),
                    options: [...s.options].map(o => o.text.trim()).slice(0,40),
                }));
                const inputs = q('input').map(i => ({type:i.type, label:lab(i)}));
                const btns = [...q('button'), ...q('a'), ...q('input[type=button]'),
                              ...q('input[type=submit]')]
                    .map(b => (b.value||b.innerText||'').trim()).filter(Boolean).slice(0,60);
                return {selects: sels, inputs: inputs, buttons: btns};
            }"""
        )
        import json as _json
        txt = DEBUG_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name}.txt"
        txt.write_text(_json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"{shot} / {txt}"
    except Exception:  # noqa: BLE001
        return shot


def request_pickup(date_label: str = "", time_label: str = "", count: int = 1,
                   headful: bool = False, dry_run: bool = False,
                   explore: bool = False, progress=None) -> dict:
    """ヤマトビジネスメンバーズの集荷依頼を自動で行う。

    - count : 集荷してもらう荷物の個数（今日の発行件数を想定）
    - date_label : 集荷希望日（例 "2026/06/14"）。空なら画面の既定
    - time_label : 集荷希望時間帯（例 "午前中"）。空なら画面の既定
    returns {"ok": bool, "message": str}
    explore=True : 集荷依頼ページの構造を b2_debug にダンプして停止（本番前の調査）
    dry_run=True : 入力まで行い、最終確定（依頼送信）はしない
    """
    import re as _re

    code = config.get_secret("YAMATO_CUSTOMER_CODE", "")
    pw = config.get_secret("YAMATO_PASSWORD", "")
    if not (code and pw):
        raise B2Error("ヤマトのログイン情報が未設定です（secrets.toml）")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pl:
        browser = _launch_chromium(pl, headful)
        ctx = browser.new_context(locale="ja-JP",
                                  viewport={"width": 1366, "height": 1000})
        page = ctx.new_page()
        try:
            _emit(progress, 20, "ヤマトにログイン中")
            page = _login_ybm(ctx, page, code, pw)
            page.wait_for_timeout(2000)
            _emit(progress, 50, "集荷依頼ページを開いています")

            # ---- 集荷依頼メニューを開く ----
            # 集荷依頼は ybmCommonJs.useService(...) で起動する隠れたリンク。
            # 非表示でも発火できるよう DOM の click() を evaluate で直接呼ぶ。
            link = page.locator('a:has-text("集荷依頼")').first
            if link.count() == 0:
                link = page.locator('a:has-text("集荷")').first
            if link.count() == 0:
                info = _dump_page(page, "pickup_menu_not_found")
                raise B2Error("集荷依頼メニューが見つかりません（調査ダンプを保存）: " + info)
            new_page = page
            try:
                with ctx.expect_page(timeout=8000) as pinfo:
                    link.evaluate("el => el.click()")  # DOMクリックでonclick発火
                new_page = pinfo.value
            except Exception:  # noqa: BLE001  同一タブ遷移（新規ページが開かない）
                pass
            page = new_page
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(4000)

            # 集荷依頼の案内ページ → 「このサービスを利用する」で実フォームへ
            use_btn = _first_visible(page, [
                'a:has-text("このサービスを利用する")',
                'button:has-text("このサービスを利用する")',
                'input[value*="このサービスを利用する"]',
            ], 8000)
            if use_btn is not None:
                np2 = page
                try:
                    with ctx.expect_page(timeout=8000) as pinfo2:
                        use_btn.click()
                    np2 = pinfo2.value
                except Exception:  # noqa: BLE001  同一タブ遷移
                    try:
                        use_btn.click()
                    except Exception:  # noqa: BLE001
                        use_btn.evaluate("el => el.click()")
                page = np2
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(4000)

            # ====== ステップ1: 集荷の日時・個数を入力 ======
            # 探索モードは妥当な既定値（最初の候補日・指定なし・1個）で埋めてから進める
            jp_date = ""
            if date_label:
                m = _re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", date_label)
                if m:
                    y, mo, d = (int(x) for x in m.groups())
                    jp_date = f"{y}年{mo}月{d}日"

            if jp_date:
                if not _select_by_name_text(page, "shukaYmd", jp_date):
                    avail = _select_options(page, "shukaYmd")
                    raise B2Error(f"集荷希望日 {jp_date} は選べません（候補: {avail}）。"
                                  "別の日を選んでください: " + _shot(page, "pickup_nodate"))
            elif explore:
                _select_first_real(page, "shukaYmd")

            tlabel = time_label or ("指定なし" if explore else "")
            if tlabel:
                _select_by_name_text(page, "shukaTimeAll", tlabel)

            page.evaluate(
                """(n) => { const el=document.querySelector('input[name=nimotsuTakkyu]');
                   if(el){ el.value=String(n);
                     el.dispatchEvent(new Event('input',{bubbles:true}));
                     el.dispatchEvent(new Event('change',{bubbles:true})); } }""",
                int(count) or 1)

            _emit(progress, 75, "集荷の日時・個数を入力中")
            page.wait_for_timeout(800)
            _shot(page, "pickup_filled")

            if explore:
                # 「次へ」だけで 確認(step3) まで進めて各画面をダンプ（最終確定はしない）
                _dump_page(page, "pickup_step1")
                _click_named(page, "action:ShukaDataInputAction_doNext")
                page.wait_for_timeout(3500)
                info2 = _dump_page(page, "pickup_step2")
                _click_named(page, "action:ShukaDataInputAction_doNext")
                page.wait_for_timeout(3500)
                info3 = _dump_page(page, "pickup_step3")
                return {"ok": False,
                        "message": f"[探索] step2={info2} / step3={info3}（確定はしていません）"}

            if dry_run:
                return {"ok": False,
                        "message": f"[確認] 個数{count}・{jp_date} {tlabel} を入力しました"
                                   "（依頼は送信していません）。画面を確認してください。"}

            # ====== ステップ1→2→3→確定 ======
            if not _click_named(page, "action:ShukaDataInputAction_doNext"):
                raise B2Error("「次へ」が見つかりません: " + _dump_page(page, "pickup_next1"))
            page.wait_for_timeout(3500)
            _shot(page, "pickup_step2")
            # 住所選択ステップにも「次へ」があれば進む
            _click_named(page, "action:ShukaDataInputAction_doNext")
            page.wait_for_timeout(3500)
            _shot(page, "pickup_step3")

            # 確認画面 → 集荷依頼を確定（最終送信）
            if not _click_pickup_confirm(page):
                raise B2Error("集荷依頼の確定ボタンが見つかりません: " + _dump_page(page, "pickup_confirm"))
            page.wait_for_timeout(4000)
            _shot(page, "pickup_done")
            return {"ok": True,
                    "message": f"集荷を依頼しました（宅急便{count}個・{jp_date} {tlabel}）"}
        finally:
            ctx.close()
            browser.close()


def _select_by_name_text(page, name: str, text: str) -> bool:
    """select[name=NAME] で、テキストに text を含むオプションを選ぶ。"""
    try:
        return bool(page.evaluate(
            """(a) => { const [n,v]=a;
                const s=document.querySelector("select[name='"+n+"']");
                if(!s) return false;
                const o=[...s.options].find(o=>o.text.includes(v));
                if(o){ s.value=o.value; s.dispatchEvent(new Event('change',{bubbles:true})); return true; }
                return false; }""", [name, text]))
    except Exception:  # noqa: BLE001
        return False


def _select_first_real(page, name: str) -> bool:
    """select[name=NAME] の最初の実オプション(index1)を選ぶ。"""
    try:
        return bool(page.evaluate(
            """(n) => { const s=document.querySelector("select[name='"+n+"']");
                if(!s||s.options.length<2) return false;
                s.selectedIndex=1; s.dispatchEvent(new Event('change',{bubbles:true})); return true; }""",
            name))
    except Exception:  # noqa: BLE001
        return False


def _select_options(page, name: str) -> list:
    """select[name=NAME] のオプション文字列一覧（エラーメッセージ用）。"""
    try:
        return page.evaluate(
            """(n) => { const s=document.querySelector("select[name='"+n+"']");
                return s ? [...s.options].map(o=>o.text.trim()) : []; }""", name)
    except Exception:  # noqa: BLE001
        return []


def _click_named(page, name: str) -> bool:
    """name属性が完全一致する要素（image/submit/button等）をクリックする。"""
    loc = page.locator("[name=\"" + name + "\"]").first
    if loc.count() == 0:
        return False
    try:
        loc.click(timeout=6000)
    except Exception:  # noqa: BLE001
        try:
            loc.evaluate("el => el.click()")
        except Exception:  # noqa: BLE001
            return False
    return True


def _click_pickup_confirm(page) -> bool:
    """確認画面で最終確定ボタン（確定）を押す。

    実画面で確認したaction名 ShukaDataConfirmAction_doDecision を優先。
    """
    import re as _re
    for nm in ['action:ShukaDataConfirmAction_doDecision',
               'action:ShukaConfirmAction_doRegist',
               'action:ShukaConfirmAction_doComplete']:
        if page.locator("[name=\"" + nm + "\"]").count() > 0:
            return _click_named(page, nm)
    for pat in [r"この内容で.*依頼", r"集荷を依頼", r"依頼を確定", r"依頼する", r"^\s*確定\s*$", r"登録"]:
        b = page.get_by_role("button", name=_re.compile(pat))
        if b.count() == 0:
            b = page.get_by_text(_re.compile(pat))
        if b.count() > 0:
            try:
                b.first.click(timeout=6000)
            except Exception:  # noqa: BLE001
                b.first.click(force=True)
            return True
    return False


def _select_option_by_text(page, label_patterns: list[str], value_text: str) -> bool:
    """ラベル（name/id/aria）が label_patterns のいずれかに一致する select で、
    value_text を部分一致するオプションを選ぶ。"""
    import json as _json
    try:
        return bool(page.evaluate(
            """(args) => {
                const [pats, val] = args;
                const res = pats.map(p => new RegExp(p));
                for (const s of document.querySelectorAll('select')) {
                    const k = (s.name||s.id||s.getAttribute('aria-label')||'');
                    if (res.some(r => r.test(k))) {
                        const o = [...s.options].find(o => o.text.includes(val));
                        if (o) {
                            s.value = o.value;
                            s.dispatchEvent(new Event('change',{bubbles:true}));
                            return true;
                        }
                    }
                }
                return false;
            }""", [label_patterns, value_text]))
    except Exception:  # noqa: BLE001
        return False
