"""Lambda エントリポイント。EventBridge から毎時起動される想定。

パイプライン:
  クォータ確認 → 取得 → 重複排除 → ソースフィルタ → 集計 → S3保存
"""
import logging
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

    result = score.aggregate(articles, now)

    # ---- 保存 ----
    store.append_jsonl(config.KEY_SERIES, [result])
    store.append_jsonl(
        f"{config.PREFIX_ARTICLES}{now.strftime('%Y-%m-%d')}.jsonl", articles
    )
    store.put_json(config.KEY_SEEN, seen)

    # 観測モード: source_domain の分布を蓄積してホワイトリスト確定に使う
    if not config.SOURCE_WHITELIST:
        obs = store.get_json(config.KEY_SOURCES, default={}) or {}
        for src, n in source_counts.items():
            obs[src] = obs.get(src, 0) + n
        store.put_json(config.KEY_SOURCES, obs)

    store.build_public(now, articles)

    state.update({
        "quota_date": today,
        "requests_used_today": used + 1,
        "last_run_utc": now.strftime("%Y%m%dT%H%M"),
        "last_success_utc": now.strftime("%Y%m%dT%H%M"),
    })
    store.put_json(config.KEY_LAST_RUN, state)

    log.info("kept=%d sentiment=%s raw=%s",
             result["n_articles"], result["sentiment"], result["raw_mean"])

    return {"status": "ok", **{k: result[k] for k in
            ("sentiment", "raw_mean", "n_articles")}}
