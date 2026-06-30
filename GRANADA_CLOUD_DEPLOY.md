# グラナダ様 月次請求 クラウド版（PCオフでもスマホで完結）デプロイ手順

毎月末日15時頃にクラウドが請求書を作成し、スマホに「送信してよいですか？」通知。
アプリで内容を確認して『送信する』を押すと、keiri@granada-jp.net へ送信されます。
**PCは起動していなくてOK。** Excel台帳は次回PC起動時に自動で追記されます。

## 仕組み（全体像）
1. **GitHub Actions**（毎月末日15:00 JST）= 集計→Excel→PDF→「承認待ち」をDB保存→スマホ通知
2. **スマホ通知**（ntfy）の「Review and Send」or アプリを開く
3. **アプリの承認ページ** `/invoice_approve` = 内容・PDFを確認→『送信する』→SMTP送信→完了通知
4. **PC常駐agent** = 起動時に、送信済み請求書をローカル `グラナダ様請求書.xlsx` へ追記

データ元は **Supabaseの出荷データ**（ヤマト伝票番号で重複排除）。金額は人が承認前に確認するので安全。

---

## デプロイ手順

### STEP 1. コードを反映（GitHub Desktop）
新規/変更ファイルを Commit → Push（数分でStreamlit Cloudへ自動反映）。
- 追加: `lib/granada_cloud.py` / `pages/invoice_approve.py` /
  `.github/workflows/granada-invoice.yml` / `templates/granada_invoice_template.xlsx`
- 変更: `requirements.txt`（openpyxl追加）/ `agent.py`（台帳同期）
> ※テンプレートxlsxには阿部農園の住所・登録番号・口座が含まれます。**privateリポジトリ前提**です。

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
- スマホに「APPROVE?」通知が来る → アプリの `/invoice_approve` を開く
- 金額・明細・PDFを確認 → 『送信する』→『はい、送信する』
- ※テストで実際に顧客へ送りたくない場合は、`CUSTOMER_EMAIL`（lib/granada_cloud.py）を一時的に自分宛にして試す

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
