"""
比赛挖掘 v2：不用 Google News，触发全源持续扫 + 自动 tag

策略：
1. 用户点"继续挖掘" → 触发所有已启用源（FFT/BBC/Guardian/Sky/90min/懂球帝/SofaScore/FIFA/ESPN/TheAnalyst/Goal）扫一轮
2. 入库后，重新对所有未来比赛做 match_tagger 关联
3. 自动累计每场比赛的关联文章数
"""
import os
import sys
import time
import logging

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("harvester")


def run_all_sources(max_per_source: int = 30):
    """跑一遍所有启用源，入库所有新文章"""
    from db import store
    from scrapers.tier1_easy import TIER1_SCRAPERS
    from scrapers.playwright_sources import PLAYWRIGHT_SCRAPERS, _patch_fetch_playwright

    _patch_fetch_playwright()  # 启用 headed 选项

    # 合并所有可用 scraper（已禁用的 google_news 不在内）
    all_scrapers = {**TIER1_SCRAPERS, **PLAYWRIGHT_SCRAPERS}

    total_new = 0
    for name, cls in all_scrapers.items():
        src = store.get_source_by_name(name)
        if not src or not src["enabled"]:
            continue
        log.info(f"  → {name}")
        try:
            s = cls()
            found, new = s.crawl(max_articles=max_per_source, skip_existing=True)
            total_new += new
            log.info(f"  ✓ {name}: +{new} 篇")
        except Exception as e:
            log.warning(f"  ✗ {name}: {e}")
        time.sleep(1)

    # 关 playwright
    try:
        from base_scraper import close_browser
        close_browser()
    except Exception:
        pass

    log.info(f"✓✓ 全源扫描完成，新增 {total_new} 篇")
    return total_new


def harvest_for_match(match: dict, target: int = 16) -> dict:
    """
    单场比赛"继续挖掘"：
    1. 跑一轮全源扫描
    2. 重新对这场比赛打标
    """
    match_id = match["id"]
    home = match.get("home_en") or match.get("home_cn") or "?"
    away = match.get("away_en") or match.get("away_cn") or "?"
    log.info(f"▶ Match #{match_id}: {home} vs {away} → 继续挖掘")

    # 跑全源
    new_count = run_all_sources(max_per_source=20)

    # 重新打标
    from match_tagger import tag_match
    linked = tag_match(match)

    return {
        "match_id": match_id,
        "home": home,
        "away": away,
        "new_articles": new_count,
        "total_linked": linked,
        "below_target": linked < target,
    }


def harvest_upcoming(days: int = 4, target: int = 16):
    """为未来 N 天所有比赛挖掘（实际上是跑一轮全源 + 重打所有标签）"""
    from db import store
    matches = store.list_upcoming_matches(days=days, only_scheduled=False)
    log.info(f"▶▶ harvest 未来 {days} 天 {len(matches)} 场")

    # 先跑全源
    run_all_sources()

    # 重打所有比赛标签
    from match_tagger import tag_all_upcoming
    tag_all_upcoming(days=days)

    log.info(f"✓✓ 完成")


if __name__ == "__main__":
    if "--match" in sys.argv:
        from db import store
        mid = int(sys.argv[sys.argv.index("--match") + 1])
        m = store.get_match(mid)
        if m:
            r = harvest_for_match(m, target=16)
            log.info(f"✓ {r}")
    else:
        days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 4
        harvest_upcoming(days=days)
