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


def fetch_and_process(days: int = 7, headful: bool = False) -> dict:
    """B2クラウドから発行済データを取得し、出荷確定まで実行する。

    returns {"rows", "shipped", "unmatched", "messages"} または raises B2Error
    """
    code = config.get_secret("YAMATO_CUSTOMER_CODE", "")
    uid = config.get_secret("YAMATO_ID", "")
    pw = config.get_secret("YAMATO_PASSWORD", "")
    if not (uid and pw):
        raise B2Error("ヤマトのログイン情報が未設定です（secrets.toml に YAMATO_ID / YAMATO_PASSWORD / YAMATO_CUSTOMER_CODE を設定）")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pl:
        browser = pl.chromium.launch(headless=not headful)
        ctx = browser.new_context(
            locale="ja-JP",
            accept_downloads=True,
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        try:
            # ---- 1. ログイン ----
            page.goto(YBM_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)

            # ログインページへ（トップにログインボタンがある場合）
            login_btn = _first_visible(page, [
                'text="ログイン"', 'a:has-text("ログイン")', 'button:has-text("ログイン")',
            ], 4000)
            if login_btn and _first_visible(page, ['input[type="password"]'], 1500) is None:
                login_btn.click()
                page.wait_for_timeout(2500)

            pw_input = _first_visible(page, ['input[type="password"]'], 12000)
            if pw_input is None:
                raise B2Error("ログイン画面が見つかりません: " + _shot(page, "login_not_found"))

            # テキスト入力欄（パスワード以外）を取得して順に埋める
            texts = page.locator(
                'input[type="text"]:visible, input[type="tel"]:visible, '
                'input[type="email"]:visible, input:not([type]):visible'
            )
            n = texts.count()
            if n >= 2 and code:
                texts.nth(0).fill(code)   # お客さまコード
                texts.nth(1).fill(uid)    # ID
            elif n >= 1:
                texts.nth(0).fill(uid)
            else:
                raise B2Error("ログイン入力欄が見つかりません: " + _shot(page, "login_inputs"))
            pw_input.fill(pw)

            submit = _first_visible(page, [
                'button:has-text("ログイン")', 'input[type="submit"]',
                'a:has-text("ログイン")', 'button[type="submit"]',
            ], 5000)
            if submit is None:
                raise B2Error("ログインボタンが見つかりません: " + _shot(page, "login_submit"))
            submit.click()
            page.wait_for_timeout(4000)

            if _first_visible(page, ['input[type="password"]'], 1500) is not None:
                raise B2Error("ログインに失敗した可能性があります（ID/パスワードを確認）: " + _shot(page, "login_failed"))

            # ---- 2. B2クラウドを開く ----
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
            except Exception:  # noqa: BLE001  同一タブで開くパターン
                pass
            b2.wait_for_load_state("domcontentloaded")
            b2.wait_for_timeout(5000)

            # ---- 3. 発行済データの検索 ----
            hist = _first_visible(b2, [
                'a:has-text("発行済データ")', 'button:has-text("発行済データ")',
                'text="発行済データの検索"', 'a:has-text("検索・再印刷")',
                'a:has-text("送り状検索")',
            ], 15000)
            if hist is None:
                raise B2Error("「発行済データ」メニューが見つかりません: " + _shot(b2, "menu"))
            hist.click()
            b2.wait_for_timeout(4000)

            search = _first_visible(b2, [
                'button:has-text("検索")', 'input[type="submit"][value*="検索"]',
                'a:has-text("検索")',
            ], 10000)
            if search:
                search.click()
                b2.wait_for_timeout(4000)

            # 全選択（ヘッダーのチェックボックス）
            try:
                chk = b2.locator('thead input[type="checkbox"], th input[type="checkbox"]').first
                if chk.count() > 0:
                    chk.check()
                    b2.wait_for_timeout(800)
            except Exception:  # noqa: BLE001
                pass

            # ---- 4. ダウンロード ----
            dl_btn = _first_visible(b2, [
                'button:has-text("ダウンロード")', 'a:has-text("ダウンロード")',
                'button:has-text("CSV")', 'a:has-text("CSV")',
                'button:has-text("出力")',
            ], 10000)
            if dl_btn is None:
                raise B2Error("ダウンロードボタンが見つかりません: " + _shot(b2, "download_btn"))

            with b2.expect_download(timeout=30000) as dlinfo:
                dl_btn.click()
                # 確認ダイアログ（OK等）が出る場合
                ok = _first_visible(b2, ['button:has-text("OK")', 'button:has-text("はい")'], 3000)
                if ok:
                    ok.click()
            download = dlinfo.value
            tmp = Path(download.path())
            raw = tmp.read_bytes()

            # ---- 5. 照合・出荷確定 ----
            result = shipping.process_issued_csv(raw)
            return result
        finally:
            ctx.close()
            browser.close()
