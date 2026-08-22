# GitHub Actions で動かす

AWS を使わない道。**やることは鍵を1つ登録して、ボタンを2回押すだけ**です。

`playbench` の `calendar.yml` と同じ型です（cron → 取得 → コミット → Pages が拾う）。
違いは、置き場を同じリポジトリの `data` ブランチにしている点だけ。

---

## 前提

| | |
|---|---|
| `sentiment_analysis` の可視性 | **public**（確認済み）。Actions の分数は無制限・無料 |
| GitHub Pages | **有効済み**（確認済み）。配信元をこの手順で `data` ブランチに向ける |
| 料金 | **0円**。Alpha Vantage も無料枠（25 req/日）に収まる |

---

## 手順

### 1. 鍵を Secrets に入れる

`sentiment_analysis` → Settings → Secrets and variables → Actions → New repository secret

| 名前 | 値 |
|---|---|
| `ALPHAVANTAGE_KEY` | <https://www.alphavantage.co/support/#api-key> で取った鍵 |

`schedule` と `workflow_dispatch` しかトリガに使っていないので、fork からの PR に
この値は渡りません（public リポジトリでも安全な形）。

### 2. Probe を1回押す ── ここが分岐点

Actions タブ → **Probe Alpha Vantage** → Run workflow

**これが通るまで先に進まないでください。** 3つ確かめます。

| 見るもの | 意味 |
|---|---|
| `✅ 到達・取得できた` | GitHub のランナー（データセンターIP）から叩ける。**BGG は同じ経路を 401 で弾いていた**ので、ここは実測でしか分からない |
| `Information: ...premium...` が出た | 無料枠では使えない。Actions でも AWS でも同じなので、方針の見直しが要る |
| `time_published は UTC` | いまの扱いで正しい |
| `4〜5時間ずれている` | US/Eastern の可能性。`src/fetch.py` の `parse_published` と `web/index.html` の `parseAV` に補正が要る |

日次25回の枠を1つ使います。

### 3. 集計を1回手で押す

Actions タブ → **Collect market sentiment** → Run workflow

初回は `data` ブランチが無いので、ワークフローが作ります。終わると
`data` ブランチに次が置かれます。

```
public/latest.json          画面が読むもの
history/sentiment.jsonl     毎時の集計（消さない）
history/articles/*.jsonl    記事の監査ログ
state/last_run.json         次の窓の起点・クォータの使用回数
state/seen.json             48時間ぶんの重複排除キャッシュ
observe/sources.json        source_domain の出現頻度
index.html                  単体SPA（web/index.html のコピー）
```

### 4. Pages の配信元を `data` ブランチにする

Settings → Pages → Source: **Deploy from a branch** → Branch: `data` / `/ (root)`

反映されると2つの URL が生きます。

```
https://shunshun0904.github.io/sentiment_analysis/                     ← 単体SPA
https://shunshun0904.github.io/sentiment_analysis/public/latest.json   ← これが endpoint
```

### 5. ホームページに URL を書く

`playbench` の `data/sentiment.js`:

```js
endpoint: 'https://shunshun0904.github.io/sentiment_analysis/public/latest.json',
```

GitHub Pages は `access-control-allow-origin: *` を返すので、**CORS の設定は要りません**
（S3 のときに必要だった `AllowedOrigin` に相当するものが丸ごと不要）。

### 6. 定期実行

**いまは止めてあります**（本番は AWS 側）。切り替えるときは
`.github/workflows/collect.yml` の `schedule` のコメントを外し、
**先に AWS の EventBridge ルールを無効化してください** ── 同じ API キーの
25req/日 を共有しているため、両方回すと枠を食い合います。 `.github/workflows/collect.yml`:

```yaml
on:
  schedule:
    - cron: '17 0,2,4,8,10,12,14,16,18,20,22 * * *'   # 2時間おき 11回/日
  workflow_dispatch:
```

11回/日に対し無料枠は25req/日なので、**予備が14回**残ります。手で押して試す余地を
残すためで、60分間隔(22回/日)にしていた時期は予備3回しかなく、プローブ1回と
手動実行だけで枯れました。

初回の取得は減衰ウィンドウと同じ24時間ぶんを一度に取るので、**1リクエストで
チャートが埋まります**（1日待つ必要はありません）。

---

## 知っておくこと

### 実行時刻は保証されない

GitHub の `schedule` はベストエフォートです。毎正時は 5〜30分の遅延が普通で、
高負荷時は run そのものが落ちることもあります。`:17` にしてあるのはそのためです。

**落ちても壊れません。** `fetch.build_window()` は「前回**成功**時刻 − 15分」を
起点にするので、次の回が空白ぶんをまとめて取り直します。

```
実測 206件/時 × 4.8時間 ≒ 990件 < AV_LIMIT (1000)
```

**およそ4.8時間ぶんまでは1リクエストで取り戻せます。** クォータ（22/25）も
落ちた回のぶん余ります。

### 60日で止まる規則には該当しない

public リポジトリの scheduled workflow は60日コミットが無いと自動停止しますが、
判定は**コミットの有無**で、これは毎時コミットするので時計が毎時リセットされます。

### リポジトリが太る

`latest.json` は81KB を毎時まるごと書き換えます。差分圧縮の効きしだいで
**年32〜128MB**。GitHub のソフト上限1GB に対して数年は持ちます。

効き始めたら、`data` を orphan ブランチにして常に1コミットで force-push すれば
履歴は増えなくなります。**データ自体は `history/sentiment.jsonl` という
ファイルの中にあるので、git 履歴を捨てても失われません。**

```bash
git checkout --orphan data-new && git add -A && git commit -m "畳む"
git push -f origin data-new:data
```

### 履歴が公開される

`data` ブランチを Pages で配信するので、`history/` も `state/` も誰でも読めます。
鍵は入っていません（`state/` は時刻と回数、`seen.json` はハッシュのみ）。
記事の監査ログが公開されて困るなら、Pages の配信を `public/` だけに絞る必要があります
── その場合は `data` ブランチには `public/` だけを置き、状態と履歴は別ブランチに分けます。

### 失敗に気づく仕掛け

GitHub は**既定でワークフロー失敗時にメールを送ります**（CloudWatch アラームの代わり）。
`python3 -m handler` は `status: error` のとき終了コード1を返すので、
「緑のまま何も取れていない」状態にはなりません。

---

## AWS に戻したくなったら

コードは両対応です。`STORE_BACKEND` を切り替えるだけで、
`handler.py` / `fetch.py` / `dedup.py` / `score.py` は1行も変わりません。

```
STORE_BACKEND=fs   ローカルのファイル（GitHub Actions）  ← 既定
STORE_BACKEND=s3   S3（AWS Lambda）                      ← infra/template.yaml が渡す
```

手順は `docs/aws-setup.md` にそのまま残してあります。
