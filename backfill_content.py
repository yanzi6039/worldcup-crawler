"""
正文回填脚本：对 content 为空或过短的文章，重新抓原文并用 readability 提取。

用法：
  python3 backfill_content.py --dry-run         # 只统计，不抓
  python3 backfill_content.py --limit 20        # 先抓 20 篇测试
  python3 backfill_content.py --source zhibo8   # 只回填某个源
  python3 backfill_content.py                   # 全量回填（约 50-80 分钟）

策略：
  1. 从 news 表找出 content 为空 / 过短的文章
  2. 按 source 分类（已知用什么 tier 抓）
  3. fetch_article(url) → readability-lxml 提取
  4. 失败的记日志，不阻塞
"""
import os
import sys
import time
import argparse
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from db import store
from base_scraper import (fetch_requests, fetch_playwright, fetch_curl_cffi,
                          extract_text, log, _throttle)
from bs4 import BeautifulSoup

# readability-lxml：Mozilla Readability 的 Python 移植
try:
    from readability import Document
    READABILITY_AVAILABLE = True
except ImportError:
    READABILITY_AVAILABLE = False
    log.warning("readability-lxml 未安装，将只用 BeautifulSoup 提取")


# 各源 tier 映射（决定用哪种 fetch 方式）
SOURCE_TIER = {
    # Tier 1: HTTP requests
    "dongqiudi": 1, "dongqiudi_team": 1, "espn_pw": 1, "theanalyst_api": 1,
    "bbc_sport": 1, "skysports": 1, "90min": 1, "guardian": 1,
    "theanalyst": 1, "flashscore": 1, "goal": 1,
    "fourfourtwo": 1, "rotowire": 1, "zhibo8": 1,
    # Tier 2: Playwright
    "sofascore": 2, "squawka": 2, "fifa": 2,
    "espn": 2, "goal_pw": 2, "kickoff": 2,
}


def fetch_by_tier(url: str, tier: int) -> str:
    """按 tier 抓 HTML"""
    if tier == 1:
        return fetch_requests(url)
    elif tier == 2:
        return fetch_playwright(url, wait_selector="article, main, .article-body, .content")
    else:
        return fetch_curl_cffi(url)


def extract_content(html: str, url: str = "") -> str:
    """提取正文：readability 优先，BeautifulSoup 兜底"""
    if not html:
        return ""

    # 方法 1: readability-lxml（对主流新闻站最准）
    if READABILITY_AVAILABLE:
        try:
            doc = Document(html)
            summary_html = doc.summary(html_partial=True)
            # 从 summary HTML 里抽纯文本（保留段落）
            soup = BeautifulSoup(summary_html, "html.parser")
            paragraphs = []
            for p in soup.find_all(["p", "h1", "h2", "h3"]):
                txt = p.get_text(" ", strip=True)
                if txt and len(txt) > 10:
                    paragraphs.append(txt)
            text = "\n\n".join(paragraphs)
            if len(text) >= 200:
                return text
        except Exception as e:
            log.debug(f"  readability fail {url[:60]}: {e}")

    # 方法 2: BeautifulSoup 通用提取（已有）
    try:
        soup = BeautifulSoup(html, "html.parser")
        return extract_text(soup)
    except Exception:
        return ""


def backfill_one(news_row: dict) -> tuple[bool, int, str]:
    """回填一篇。返回 (是否成功, 新正文长度, 错误信息)"""
    url = news_row["url"]
    source_name = news_row["source_name"]
    tier = SOURCE_TIER.get(source_name, 1)

    try:
        html = fetch_by_tier(url, tier)
        if not html:
            return False, 0, "fetch failed"

        content = extract_content(html, url)
        if len(content) < 100:
            return False, len(content), f"too short ({len(content)})"

        # 入库
        with store.conn_ctx() as conn:
            conn.execute(
                "UPDATE news SET content=? WHERE id=?",
                (content[:30000], news_row["id"])
            )
        return True, len(content), ""
    except Exception as e:
        return False, 0, str(e)[:100]


def main():
    p = argparse.ArgumentParser(description="回填缺正文的文章")
    p.add_argument("--limit", type=int, default=0, help="限制条数（0=全部）")
    p.add_argument("--source", type=str, default="", help="只回填某个源")
    p.add_argument("--min-length", type=int, default=100, help="content 长度小于此值视为缺失")
    p.add_argument("--dry-run", action="store_true", help="只统计，不实际抓取")
    args = p.parse_args()

    # 找出缺正文的文章
    with store.conn_ctx() as conn:
        sql = """
            SELECT id, url, source_name, title,
                   length(COALESCE(content, '')) AS clen
            FROM news
            WHERE COALESCE(length(content), 0) < ?
              AND url != ''
        """
        params = [args.min_length]
        if args.source:
            sql += " AND source_name = ?"
            params.append(args.source)
        sql += " ORDER BY id"
        if args.limit > 0:
            sql += f" LIMIT {args.limit}"

        rows = conn.execute(sql, params).fetchall()

    log.info(f"找到 {len(rows)} 篇缺正文的文章" +
             (f"（源: {args.source}）" if args.source else ""))

    if not rows:
        log.info("没有需要回填的文章，退出")
        return

    if args.dry_run:
        # 按源统计
        from collections import Counter
        by_source = Counter(r["source_name"] for r in rows)
        log.info("按源分布：")
        for src, cnt in by_source.most_common():
            log.info(f"  {src:20} {cnt}")
        return

    # 逐篇回填
    ok_count = 0
    fail_count = 0
    started = time.time()

    for i, row in enumerate(rows, 1):
        log.info(f"[{i}/{len(rows)}] #{row['id']} {row['source_name']:15} {row['title'][:50]}")
        ok, new_len, err = backfill_one(row)
        if ok:
            ok_count += 1
            log.info(f"  ✓ {new_len} 字")
        else:
            fail_count += 1
            log.warning(f"  ✗ {err}")

        # 每 10 篇打印进度
        if i % 10 == 0:
            elapsed = time.time() - started
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(rows) - i) / rate if rate > 0 else 0
            log.info(f"  📊 进度: {ok_count} ok / {fail_count} fail / "
                     f"速率 {rate:.1f} 篇/秒 / ETA {eta/60:.1f} 分钟")

    elapsed = time.time() - started
    log.info(f"\n{'='*60}")
    log.info(f"✓ 完成: {ok_count} 成功 / {fail_count} 失败 / 共 {len(rows)} 篇 / {elapsed/60:.1f} 分钟")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(BASE_DIR, "logs", "backfill.log"),
                                encoding="utf-8"),
        ],
    )
    os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
    main()
