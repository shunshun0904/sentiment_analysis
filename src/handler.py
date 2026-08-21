"""パイプラインの結線。60分に1回、外から呼ばれる想定。

  クォータ確認 → 取得 → 重複排除 → ソースフィルタ → 集計 → 保存

入口は2つあるが、通る道は同じ:
  AWS Lambda      lambda_handler(event, context)  ← EventBridge が呼ぶ
  GitHub Actions  python3 -m handler              ← 下の __main__ が呼ぶ
"""
import json
import logging
import sys
from datetime import datetime

import config
import dedup
import fetch
import score
import store

log = logging.getLogger()
log.setLevel(logging.INFO)


def lambda_handler(event, context):
    now = store.utcnow()
    today = now.strftime("%Y-%m-%d")

    if now.hour in config.SKIP_HOURS_UTC:
        log.info("skip hour %s UTC (quota conservation)", now.hour)
        return {"status": "skipped", "reason": "quiet_hour"}

    state = store.get_json(config.KEY_LAST_RUN, default={}) or {}

    try:
        used = fetch.check_quota(state, today)
    except fetch.QuotaExceeded as e:
        log.error("%s", e)
        return {"status": "skipped", "reason": "quota"}

    last_run = None
    if state.get("last_success_utc"):
        last_run = datetime.strptime(state["last_success_utc"], "%Y%m%dT%H%M").replace(
            tzinfo=now.tzinfo
        )
    time_from, time_to = fetch.build_window(last_run, now)

    api_key = store.get_api_key()
    try:
        body = fetch.fetch_news(api_key, time_from, time_to)
    except fetch.FetchError as e:
        # 失敗してもクォータは消費されている前提でカウントする
        state.update({"quota_date": today, "requests_used_today": used + 1,
                      "last_run_utc": now.strftime("%Y%m%dT%H%M")})
        store.put_json(config.KEY_LAST_RUN, state)
        log.error("fetch failed: %s", e)
        return {"status": "error", "reason": str(e)}

    feed = body.get("feed", [])
    log.info("window %s..%s returned %s items", time_from, time_to, body.get("items"))

    seen = store.get_json(config.KEY_SEEN, default={"urls": {}, "titles": {}})
    articles, source_counts = dedup.filter_articles(feed, seen, now)
    seen = dedup.prune_seen(seen, now)

    # ---- 保存 ----
    # 集計より先に記事を書く。集計は「今回取った記事」ではなく
    # **減衰ウィンドウ（24時間）に入っている全記事**を対象にするので、
    # 今回ぶんも含めて読み直せる状態にしてから測る。
    #
    # ここを articles にすると、S(t) の定義（24時間の加重平均）から外れるうえ、
    # 重複排除で新着が0件になった回に current が null に落ちる。
    store.append_jsonl(
        f"{config.PREFIX_ARTICLES}{now.strftime('%Y-%m-%d')}.jsonl", articles
    )
    window = store.window_articles(now)
    result = score.aggregate(window, now)

    store.append_jsonl(config.KEY_SERIES, [result])
    store.put_json(config.KEY_SEEN, seen)

    # 観測モード: source_domain の分布を蓄積してホワイトリスト確定に使う
    if not config.SOURCE_WHITELIST:
        obs = store.get_json(config.KEY_SOURCES, default={}) or {}
        for src, n in source_counts.items():
            obs[src] = obs.get(src, 0) + n
        store.put_json(config.KEY_SOURCES, obs)

    # window は読み直したものをそのまま渡す（同じものを2度読まない）
    store.build_public(now, articles, window=window)

    state.update({
        "quota_date": today,
        "requests_used_today": used + 1,
        "last_run_utc": now.strftime("%Y%m%dT%H%M"),
        "last_success_utc": now.strftime("%Y%m%dT%H%M"),
    })
    store.put_json(config.KEY_LAST_RUN, state)

    log.info("kept=%d window=%d sentiment=%s raw=%s",
             len(articles), result["n_articles"], result["sentiment"], result["raw_mean"])

    return {"status": "ok", **{k: result[k] for k in
            ("sentiment", "raw_mean", "n_articles")}}


if __name__ == "__main__":
    # GitHub Actions からの入口。Lambda と同じ関数をそのまま通す。
    # 失敗は終了コードに出す ── ワークフローが緑のまま何も取れていない、
    # という状態を作らないため。
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = lambda_handler({}, None)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(1 if result.get("status") == "error" else 0)
