# クラウド公開の手順（阿部農園 精米・発送管理システム）

スマホからも使えるように、インターネット上にアプリを公開します。
**すべて無料**で、所要およそ 30〜60分。上から順に進めてください。

公開すると、こうなります：
- スマホ・PCどちらのブラウザからも同じ画面が使える
- データはクラウドに保存（スマホで入れた注文がPCでも見える）
- **PCが消えていてもスマホから「ヤマトCSVを予約」でき、PCを起動すると自動で『ヤマト出荷CSV』フォルダに保存される**

---

## STEP 1. GitHub アカウントを作る（コードの置き場所）

1. https://github.com/signup をひらく
2. メールアドレス・パスワード・ユーザー名を決めて登録（無料）

### GitHub Desktop でこのフォルダをアップ
3. https://desktop.github.com/ から **GitHub Desktop** をインストールし、STEP1のアカウントでサインイン
4. 上部メニュー **File → Add Local Repository** → この `sisakku` フォルダを選ぶ
5. 右下の **Publish repository** を押す
   - **「Keep this code private」に必ずチェック**（お客様情報を扱うため）
   - Publish を押すとアップ完了

> 顧客情報CSV・パスワードなどは自動的に除外（`.gitignore`）されるので、アップされません。

---

## STEP 2. Supabase でデータベースを作る（無料）

1. https://supabase.com/ → **Start your project** → GitHubアカウントでサインイン
2. **New project** を作成
   - Name：`abe-rice`（任意）
   - **Database Password** を決めて**メモ**（後で使います）
   - Region：`Northeast Asia (Tokyo)` がおすすめ
3. 作成完了後、左メニュー **⚙ Project Settings → Database** を開く
4. **Connection string** の **「Session pooler」** タブを選び、表示されたURIをコピー
   - 形：`postgresql://postgres.xxxx:【パスワード】@aws-0-...pooler.supabase.com:5432/postgres`
   - `【パスワード】`の部分を、STEP2-2で決めたパスワードに置き換える
   - ※「Session pooler」を使うのが安定します（うまく繋がらない時はこちら）

---

## STEP 3. Streamlit でアプリを公開する（無料）

1. https://share.streamlit.io/ → **Continue with GitHub** でサインイン
2. **Create app → Deploy a public app from a repo**（プライベートも可）
3. 設定
   - Repository：STEP1で作った `sisakku` リポジトリ
   - Branch：`main`（または `master`）
   - **Main file path：`Home.py`**
4. **Advanced settings → Secrets** に、次を貼り付ける（値は自分のものに置換）：
   ```toml
   DATABASE_URL = "STEP2でコピーしたURI"
   APP_PASSWORD = "好きなログインパスワード"
   ```
5. **Deploy** を押す。数分でURLが発行されます（例：`https://abe-rice.streamlit.app`）

---

## STEP 4. 最初の準備（公開後に1回だけ）

発行されたURLをスマホ／PCで開く →（パスワードでログイン）

1. **⚙ 設定 → 📮 送り主**：阿部さんの住所・電話を確認して保存
2. **⚙ 設定 → 🗃 データ管理 → 顧客データの再取込**：
   手元の `発行済データ.csv` をアップロード → **お客様一覧が登録される**
   （顧客情報はクラウドDBにのみ入り、GitHubには上がりません）
3. （任意）**⚙ 設定 → 🔗 BASE API**：BASEの自動取込・自動出荷を使うなら認証情報を登録

スマホのブラウザで開いて「ホーム画面に追加」しておくと、アプリのように使えます。

---

## STEP 5. PCを「予約の受け取り役」にする（自動出力）

スマホで予約した出力を、PC起動時に自動で『ヤマト出荷CSV』へ保存させます。

1. この `sisakku` フォルダの `.streamlit` の中の **`secrets.toml.example`** を
   コピーして **`secrets.toml`** にリネーム
2. 中身を STEP3 と同じ内容（`DATABASE_URL` と `APP_PASSWORD`）にする
3. **`スタートアップ登録.bat`** をダブルクリック（1回だけ）

これで、PCを起動するたびに `agent.py` がクラウドの予約を確認し、
`デスクトップ\ヤマト出荷CSV\YYYYMMDD_ヤマト出荷用出力データ.csv` を自動作成します。

> PCローカルの `run.bat` も、`secrets.toml` を置けば**クラウドと同じデータ**で動きます
> （PC・スマホ・クラウドのデータが1つに統一されます）。

---

## 困ったとき
- **ログインできない**：Secrets の `APP_PASSWORD` と入力が一致しているか確認
- **データベースに繋がらない**：Supabaseは「Session pooler」のURIを使う／`【パスワード】`を置換したか確認
- **顧客が空**：STEP4-2 のCSVアップロードを実施
- **コードを直したら**：GitHub Desktop で Commit → Push すると、数分で自動的にクラウドへ反映

---

## スマホで「アプリのように」使う（PWA対応の現状）

`lib/ui.py` の `_inject_pwa()` で、ホーム画面に追加したときアプリ風に
起動できるようにしています。Streamlit Community Cloud は配信HTMLの
`<head>` を書き換えられず、ルート直下に静的ファイル（manifest.json /
service-worker.js）も置けないため、**同一オリジンの極小コンポーネント
iframe からJSで親ページの `<head>` にタグを流し込む**方式を採っています。

### できていること
- **iPhone（Safari）**：`apple-touch-icon`（専用アイコン）、
  `apple-mobile-web-app-capable`（アドレスバー無しの全画面）、
  `apple-mobile-web-app-title`（"精米・発送"）、`theme-color`（濃紺）。
- **Android（Chrome）**：`manifest`（blob配信・`start_url`/`scope` は
  起動時に実URLから絶対パスで補完）、アイコン192/512、
  `display: standalone`、テーマ/背景色＝濃紺。インストール可。
- 追加手順は **設定 → データ → 「ホーム画面に追加」** に記載。

### できないこと（Service Worker／オフライン・プッシュ）
**現在のホスティング（Streamlit Community Cloud）では不可能**です。理由：
- Service Worker のスクリプトは **http(s) かつ JS の Content-Type で配信**
  される必要があり、`blob:` / `data:` URL からの `register()` はブラウザが
  拒否する。Cloud では root に `sw.js` を置けず、レスポンスヘッダ
  （`Service-Worker-Allowed` 等）も制御できない。
- iframe 経由で登録しても **スコープが iframe のパスに限定**され、アプリ
  本体（ルート）を制御できない。
- → オフライン動作やプッシュ通知が必要になったら、**静的ファイルと
  ヘッダを自前で制御できるホスティング**（自前サーバ＋リバースプロキシ、
  Render / Fly.io / VPS など）へ移すことが前提。深追いは不要。
