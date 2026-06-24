"""
比赛打标器
扫所有新闻 → 用 match_keywords 判断每篇属于哪些比赛 → 写入 news_match_links

用法：
  python3 match_tagger.py             # 给所有新闻重新打 match 标
  python3 match_tagger.py --match 123 # 只给某场比赛找新闻
"""
import os
import sys
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from db import store
from match_keywords import generate_match_keywords, score_article_for_match

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("match_tagger")


def tag_match(match: dict) -> int:
    """为单场比赛找关联新闻，返回关联数（先清旧链接再重建）"""
    match_id = match["id"]
    kws = generate_match_keywords(match)

    home_label = match.get("home_en") or match.get("home_cn") or f"team{match['home_country_id']}"
    away_label = match.get("away_en") or match.get("away_cn") or f"team{match['away_country_id']}"

    # ★ 先清掉该场比赛的所有旧链接，再重建（防止旧错误链接残留）
    with store.conn_ctx() as conn:
        conn.execute("DELETE FROM news_match_links WHERE match_id=?", (match_id,))

    # 拿所有新闻（含正文）
    all_news = store.list_news(limit=10000, include_content=True)
    log.info(f"  match #{match_id} {home_label} vs {away_label}: 扫 {len(all_news)} 篇新闻")

    linked = 0
    tier_counts = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
    for n in all_news:
        relevance = score_article_for_match(n.get("title", ""), n.get("content", ""), kws)
        tier = relevance.get("tier")
        if tier:
            store.insert_match_link(n["id"], match_id, tier, relevance.get("score", 1.0))
            linked += 1
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

    below = linked < 16
    store.update_match_target(match_id, below)
    log.info(f"  ✓ 关联 {linked} 篇 (A={tier_counts['A']}/B={tier_counts['B']}/"
             f"C={tier_counts['C']}/D={tier_counts['D']}/E={tier_counts['E']})"
             f" {'⚠ 不足 16' if below else '✅'}")
    return linked


def tag_all_upcoming(days=4):
    """给未来 N 天所有比赛打标"""
    matches = store.list_upcoming_matches(days=days, only_scheduled=False)
    log.info(f"▶ 给未来 {days} 天 {len(matches)} 场比赛打标")
    total_linked = 0
    below_count = 0
    for m in matches:
        n = tag_match(m)
        total_linked += n
        if n < 16:
            below_count += 1
    log.info(f"✓ 完成：{len(matches)} 场，总关联 {total_linked} 篇，{below_count} 场不足 16")


def retag_all():
    """清空 + 重建所有 match 标签"""
    with store.conn_ctx() as conn:
        conn.execute("DELETE FROM news_match_links")
    log.info("✓ 清空 news_match_links")
    tag_all_upcoming(days=30)  # 全部比赛


if __name__ == "__main__":
    if "--match" in sys.argv:
        idx = sys.argv.index("--match")
        mid = int(sys.argv[idx + 1])
        m = store.get_match(mid)
        if m:
            tag_match(m)
    elif "--all" in sys.argv:
        retag_all()
    else:
        tag_all_upcoming(days=4)
