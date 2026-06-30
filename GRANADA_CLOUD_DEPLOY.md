# 月次請求 クラウド版（PCオフでもスマホで完結・複数取引先対応）デプロイ手順

毎月末日15時頃にクラウドが**全請求先**の請求書を作成し、スマホに「送信してよいですか？」通知。
通知から**出荷アプリの「請求」タブ**へ飛び、内容とPDFを確認して『送信する』で送付されます。
**PCは起動していなくてOK。** Excel台帳は次回PC起動時に自動で追記されます。

## 仕組み（全体像）
1. **GitHub Actions**（毎月末日15:00 JST）= 全請求先を集計→Excel→PDF→「承認待ち」をDB保存→スマホ通知
2. **スマホ通知**（ntfy）の「Open app」or アプリを開く（リンクは `?tab=billing`）
3. **アプリの「請求」タブ** = 各請求先の内容・PDFを確認→『送信する』→『はい』→SMTP送信→完了通知
4. **PC常駐agent** = 起動時に、送信済み請求書を各請求先の `local_xlsx` 台帳へ追記

請求先は **アプリの「請求」→「請求先マスタ」** で追加・編集（宛名・メール・集計対象顧客・単価・文面・書類番号）。
データ元は **Supabaseの出荷データ**（ヤマト伝票番号で重複排除）。金額は人が承認前に確認するので安全。

---

## デプロイ手順

### STEP 1. コードを反映（GitHub Desktop）
新規/変更ファイルを Commit → Push（数分でStreamlit Cloudへ自動反映）。
- 追加: `lib/billing.py`（汎用エンジン）/ `lib/granada_cloud.py`（低レベル共通関数）/
  `.github/workflows/granada-invoice.yml` / `templates/granada_invoice_template.xlsx`
- 変更: `Home.py`＋`lib/ui.py`（「請求」タブ追加）/ `requirements.txt`（openpyxl）/ `agent.py`（台帳同期）
> ※テンプレートxlsxには阿部農園の住所・登録番号・口座が含まれます。**privateリポジトリ前提**です。
> ※請求先マスタは初回アクセス時に既定（グラナダ様）が自動登録されます。

### STEP 2. Streamlitアプリの Secrets に送信設定を追加
share.streamlit.io → 対象アプリ → **Settings → Secrets** に追記（既存の DATABASE_URL / APP_PASSWORD はそのまま）：
```toml
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = "587"
SMTP_USER = "abekeyn@gmail.com"
SMTP_PASS = "（Gmailアプリパスワード16桁・スペース無し）"
MAIL_FROM = "abekeyn@gmail.com"
NTFY_TOPIC = "abe-rice-farm-claude"
```

### STEP 3. GitHub Actions の Secrets を登録
GitHubリポジトリ → **Settings → Secrets and variables → Actions → New repository secret** を3つ：
- `DATABASE_URL` … Streamlitと同じSupabaseのURI
- `APP_URL` … アプリのURL（例 `https://abe-rice.streamlit.app`）
- `NTFY_TOPIC` … `abe-rice-farm-claude`

### STEP 4. 動作テスト（任意の日でOK）
GitHubリポジトリ → **Actions → 「グラナダ様 月次請求」→ Run workflow**（手動実行）。
- スマホに「APPROVE?」通知が来る → アプリを開く（「請求」タブが自動選択）
- 金額・明細・PDFを確認 → 『送信する』→『はい、送信する』
- ※テスト送信先を変えたい場合は、アプリ「請求先マスタ」で対象請求先のメールを一時的に自分宛にして試す

---

## 既存PC版との関係
- 既に登録済みのWindowsタスク `AbeFarm-GranadaInvoice-Monthly`（末日18:00・PC必要・即送信）は
  **保険**として残せます。クラウド版に一本化するなら、PC版タスクは無効化してください：
  PowerShell `Disable-ScheduledTask -TaskName AbeFarm-GranadaInvoice-Monthly`
  （二重送信はクラウド側の承認制＋PC版の送信済みマーカーで基本的に起きませんが、運用を1本にするのが安全）

## 注意・前提
- 送信前に必ず**人が承認**するので、集計ミスや同期漏れはその場で気づけます（送信されません）。
- Supabaseに当月の出荷が未取込だと件数が少なく出ます→承認画面に警告表示。PCで出荷取込後に再実行。
- ヤマトのサービス時間は関係ありません（クラウド版はDB集計のためログイン不要）。
