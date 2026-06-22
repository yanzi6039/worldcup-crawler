"""
新闻打标：基于关键词匹配，给每条新闻关联国家/球员
v0：纯关键词；v1：可加 LLM 实体识别
"""
import os
import sys
import json
import re
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from db import store

log = logging.getLogger("tagger")

# 缓存关键词索引：keyword_lower -> (type, id)
_KW_INDEX = None


def _build_index():
    global _KW_INDEX
    if _KW_INDEX is not None:
        return _KW_INDEX
    idx = {}
    with store.conn_ctx() as conn:
        # 国家
        for row in conn.execute("SELECT id, keywords FROM countries WHERE qualified=1"):
            cid = row["id"]
            for kw in json.loads(row["keywords"] or "[]"):
                # 关键词规范化（去掉空格、小写）
                k = _normalize(kw)
                if len(k) >= 2:
                    idx.setdefault(k, []).append(("country", cid))
        # 球员
        for row in conn.execute("SELECT id, keywords, name_en, name_cn FROM players"):
            pid = row["id"]
            kws = json.loads(row["keywords"] or "[]")
            # 把 name_en / name_cn 也加入
            if row["name_en"]:
                kws.append(row["name_en"])
            if row["name_cn"]:
                kws.append(row["name_cn"])
            for kw in kws:
                k = _normalize(kw)
                if len(k) >= 3:  # 球员关键词至少 3 字符，避免 "Son" 等过短误匹配
                    idx.setdefault(k, []).append(("player", pid))
    _KW_INDEX = idx
    log.info(f"  tagger index built: {len(idx)} keywords")
    return idx


def _normalize(s: str) -> str:
    """关键词小写 + 去空格 + 去标点"""
    if not s:
        return ""
    s = s.lower().strip()
    # 去掉常见后缀's 等
    return s


def tag_article(news_id: int, title: str, content: str):
    """
    对一条新闻打标
    - 国家：**只在标题里匹配**（正文容易提及其他国家导致误匹配）
    - 球员：在标题 + 正文前 3000 字匹配
    """
    idx = _build_index()
    title_norm = _normalize(title or "")
    body_norm = _normalize((content or "")[:3000])

    countries_hits = set()
    players_hits = set()

    for kw, targets in idx.items():
        if not kw:
            continue
        # 国家：只看标题
        in_title = kw in title_norm
        # 球员：标题或正文
        in_body = kw in body_norm
        if not (in_title or in_body):
            continue
        for typ, tid in targets:
            if typ == "country":
                if in_title:  # 国家要求标题命中
                    countries_hits.add(tid)
            else:
                players_hits.add(tid)

    if not countries_hits and not players_hits:
        return

    with store.conn_ctx() as conn:
        for cid in countries_hits:
            conn.execute("""
                INSERT OR IGNORE INTO news_country_links (news_id, country_id)
                VALUES (?, ?)
            """, (news_id, cid))
        for pid in players_hits:
            conn.execute("""
                INSERT OR IGNORE INTO news_player_links (news_id, player_id)
                VALUES (?, ?)
            """, (news_id, pid))
    log.debug(f"  news #{news_id}: {len(countries_hits)} countries, {len(players_hits)} players")


def rebuild_all_tags():
    """重建所有新闻的标签（数据修复用）"""
    with store.conn_ctx() as conn:
        # 清空
        conn.execute("DELETE FROM news_country_links")
        conn.execute("DELETE FROM news_player_links")
        rows = conn.execute("SELECT id, title, content FROM news").fetchall()

    log.info(f"  retagging {len(rows)} articles...")
    for r in rows:
        tag_article(r["id"], r["title"], r["content"] or "")
    log.info("  ✓ retag done")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    rebuild_all_tags()
