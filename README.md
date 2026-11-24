# X → Instagram 自動転載bot

Xに投稿した **自分の投稿のうち、写真または動画が付いたもの** を、Instagramに自動で転載します。

- 転載するもの：写真付き（1〜4枚）、動画付き、GIF付きの自分の投稿
- 転載しないもの：文字だけの投稿、リポスト、引用リポスト、リプライ
- 写真1枚 → 通常の画像投稿／動画1本 → リール（フィードにも表示）／複数 → カルーセル投稿
- 本文はXの文章をそのまま使います（長文ポストは全文）。添付画像へのリンク（pic.x.com）は消し、その他のリンクは元のURLに戻します
- A4チラシのような縦長画像はInstagramの比率（4:5）に収まらないため、**画像を切らずに左右へ白い余白を足して** 投稿します
- GitHub上で5分ごとに自動実行されます（パソコンの電源を切っていても動きます）。転載までの目安は5〜15分です

---

## 準備するもの

| | 内容 | 費用の目安 |
|---|---|---|
| GitHubアカウント | botを動かす場所 | 無料 |
| X 開発者アカウント | 自分の投稿を読み取るため | 自分の投稿の読み取りは1件0.001ドル。月数十円程度 |
| Instagram プロアカウント | 「クリエイター」か「ビジネス」 | 無料 |
| Meta for Developers アカウント | Instagramに投稿するため | 無料 |

---

## セットアップ手順

### 1. GitHubにリポジトリを作る

1. GitHubで新しいリポジトリを作成（例：`x2ig`）。**Public（公開）** にしてください
   - 縦長画像を加工したとき、Instagramが画像を取りに来られる場所としてこのリポジトリを使うためです
   - 公開されるのはプログラム・転載済み投稿のID・加工した画像だけで、どれも元々Xで公開している内容です。パスワードやキーは後述の「Secrets」に入れるので公開されません
   - Publicだと GitHub Actions も無料・無制限で使えます
2. このフォルダの中身をすべてアップロードします。**GitHub Desktop** を使うのが確実です
   - Macでは `.github` フォルダが隠しファイルになっています。Finderで `⌘ + Shift + .` を押すと表示されます。これがないと自動実行されません

### 2. X のキーを取得する

1. https://console.x.com にログインし、アプリ（App）を作成
2. User authentication settings で権限を **Read** に設定
3. 次の4つを発行してメモします（自分のアカウント用のAccess Tokenを発行）
   - API Key / API Key Secret
   - Access Token / Access Token Secret
4. Developer Console で少額のクレジットを購入しておきます（残高がないとAPIが動きません）

### 3. Instagram のトークンを取得する

1. Instagramアプリの設定で、アカウントを **プロアカウント（クリエイター or ビジネス）** に切り替え
2. https://developers.facebook.com でアプリを作成し、「Instagram」を追加 → **「Instagramログインによる API 設定」** を選択
3. 「アカウントを追加」で自分のInstagramアカウントを連携
   - アプリの「役割」→「Instagramテスター」に自分のアカウントを追加し、Instagram側（設定 → ウェブサイトのアクセス許可 → アプリとウェブサイト → テスターへの招待）で承認が必要な場合があります
4. 画面に表示される **InstagramユーザーID** と **アクセストークン** をメモ
   - 権限は `instagram_business_basic` と `instagram_business_content_publish` が必要です

※ Metaの管理画面は頻繁に表示が変わります。名称が多少違っても、上の流れで進めてください。

### 4. GitHub用の個人トークンを作る（Instagramトークンの自動更新用）

Instagramのトークンは60日で切れるので、毎週自動で更新する仕組みにしています。そのためにGitHubのトークンが1つ必要です。

1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token
2. Repository access：このリポジトリだけを選択
3. Permissions：**Secrets を Read and write** にする
4. 有効期限は選挙が終わるまで持つ日付に設定

### 5. Secrets を登録する

リポジトリの Settings → Secrets and variables → Actions → **New repository secret** で、次の7つを登録します。

| 名前 | 中身 |
|---|---|
| `X_API_KEY` | XのAPI Key |
| `X_API_SECRET` | XのAPI Key Secret |
| `X_ACCESS_TOKEN` | XのAccess Token |
| `X_ACCESS_TOKEN_SECRET` | XのAccess Token Secret |
| `IG_USER_ID` | InstagramユーザーID |
| `IG_ACCESS_TOKEN` | Instagramのアクセストークン |
| `GH_PAT` | 手順4で作ったGitHubトークン |

### 6. 動作確認

1. リポジトリの **Actions** タブ →「X → Instagram 転載」→ **Run workflow**（「テスト実行」にチェックが入った状態で）
   - 初回は「基準点を記録しました」と出て終わります。**過去の投稿をまとめて転載することはありません**。これ以降の投稿が対象です
2. Xに写真付きのテスト投稿をする
3. もう一度「テスト実行」で Run workflow → ログに転載予定の本文と画像URLが出ればOK
4. 「テスト実行」のチェックを外して Run workflow → Instagramに投稿されれば完了
5. あとは5分ごとに自動で動きます

「Instagram トークン更新」のほうも一度手動で Run workflow して、成功するか確認しておいてください。

---

## 日々の使い方・設定変更

設定は `config.json` をGitHubの画面上で編集するだけで変えられます。

```json
{
  "paused": false,
  "pause_dates": [],
  "caption_footer": "",
  "pad_color": "#FFFFFF",
  "max_posts_per_run": 5,
  "graph_api_version": "v23.0"
}
```

| 項目 | 意味 |
|---|---|
| `paused` | `true` にすると転載を一時停止 |
| `pause_dates` | 転載しない日（日本時間）。例：`["2027-04-25"]` |
| `caption_footer` | Instagramの本文末尾に毎回付ける文（ハッシュタグなど）。例：`"#武蔵野市 #市民参加"` |
| `pad_color` | 縦長画像に足す余白の色。例：オレンジなら `"#F39800"` |
| `max_posts_per_run` | 1回の実行で転載する上限 |

### 急いでInstagramに載せたいとき

Xに投稿したあと、GitHubのサイトかアプリで **Actions →「X → Instagram 転載」→ Run workflow** を押します。**「テスト実行」のチェックは外してください。** 1〜2分で転載されます。

（スマホならブラウザでGitHubを開けば同じ操作ができます。ブックマークしておくと便利です）

**停止中にXへ投稿したものは、再開後も転載されません。** 投票日の投稿が翌日以降にInstagramへ流れるのを防ぐためです。

### ⚠️ 投票日について

公職選挙法では、投票日当日にネット上で選挙運動の更新をすることはできません。**投票日が決まったら、必ず `pause_dates` にその日付を入れてください。** 告示後の運用ルールの細かい点は、武蔵野市選挙管理委員会に確認しておくと安心です。

---

## 知っておいてほしい制限

- **転載のタイミング**：5分おきに確認しますが、GitHubが混んでいると10〜20分遅れることがあります
- **Instagramの本文のリンクは押せません**（Instagramの仕様）。URLは文字として残ります
- **Xで投稿を編集・削除しても、Instagram側には反映されません**。必要ならInstagramで直接直してください
- **動画がInstagramのリールの条件に合わない場合**（3秒未満など）は投稿に失敗します。失敗時は3回まで再試行し、それでもダメならその投稿は飛ばします
- **失敗するとGitHubからメール通知** が届きます。Actionsタブのログで理由が確認できます
- Instagramの投稿上限は24時間で50件です（普段の使い方なら問題ありません）
- 60日以上リポジトリに動きがないとGitHubが定期実行を止めますが、トークン更新のワークフローが毎週記録を残すので止まりません

## ファイル構成

| ファイル | 役割 |
|---|---|
| `bot.py` | 本体 |
| `config.json` | 設定 |
| `state.json` | どこまで転載したかの記録（自動で作成・更新） |
| `refresh_token.py` | Instagramトークンの更新 |
| `.github/workflows/crosspost.yml` | 5分ごとの自動実行 |
| `.github/workflows/refresh-token.yml` | 毎週のトークン更新 |
| `media/` | 余白を足した画像の置き場（自動） |
| `tests/` | 動作確認用のテスト |
