"""
SQLite 读写封装
单连接，线程不安全；调用方负责并发隔离
"""
import os
import sys
import json
import sqlite3
import hashlib
import time
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config import DB_PATH


def get_conn():
    """获取连接（row_factory=Row 方便按字段名访问）"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 写并发友好
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def conn_ctx():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============ 新闻 ============

def url_hash(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def news_exists(url: str) -> bool:
    h = url_hash(url)
    with conn_ctx() as conn:
        row = conn.execute("SELECT 1 FROM news WHERE url_hash=? LIMIT 1", (h,)).fetchone()
        return row is not None


def insert_news(article: dict) -> int | None:
    """
    插入一条新闻。三层去重：
    - URL hash（完全相同 URL）
    - title_hash（标题归一化相同）
    - content minhash jaccard >= 0.85（正文高度相似）
    重复返回 None，新插入返回 id
    """
    # 三层去重
    try:
        from dedup import find_duplicate, remember
        dup = find_duplicate(
            article["url"],
            article.get("title", ""),
            article.get("content", ""),
        )
        if dup:
            return None  # 重复，跳过
    except Exception as e:
        # dedup 出错不阻塞，降级到 URL 去重
        if news_exists(article["url"]):
            return None

    h = url_hash(article["url"])
    # 计算 title_hash 和 signature
    try:
        from dedup import title_hash, content_signature, signature_to_str
        th = title_hash(article.get("title", ""))
        sig_str = signature_to_str(content_signature(article.get("content", ""))) \
                  if article.get("content") else ""
    except Exception:
        th, sig_str = "", ""

    with conn_ctx() as conn:
        cur = conn.execute("""
            INSERT INTO news
                (url, url_hash, title, title_hash, signature, summary, content,
                 source_id, source_name, author, published_at, language,
                 image_url, is_match_report)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            article["url"], h, article["title"], th, sig_str,
            article.get("summary"), article.get("content"),
            article.get("source_id"), article["source_name"],
            article.get("author"), article.get("published_at"),
            article.get("language"), article.get("image_url"),
            1 if article.get("is_match_report") else 0,
        ))
        new_id = cur.lastrowid

    # 记到 dedup 缓存
    try:
        from dedup import remember
        remember(article["url"], article.get("title", ""), article.get("content", ""))
    except Exception:
        pass

    # 推送到爬虫控制台（含标题+时间+来源）
    if new_id:
        try:
            from web.crawl_status import crawl_status
            pub = (article.get('published_at') or '')[:16]
            src = article.get('source_name', '')
            title = article.get('title', '')[:60]
            crawl_status.log_event(f"  📰 [{src}] {pub} {title}", "title")
        except: pass
    return new_id


def update_source_crawl(source_id: int, new_count: int):
    with conn_ctx() as conn:
        conn.execute("""
            UPDATE sources SET last_crawled_at=CURRENT_TIMESTAMP, crawl_count=crawl_count+?
            WHERE id=?
        """, (new_count, source_id))


def log_crawl(source_id: int, source_name: str, found: int, new: int,
              status: str = "success", error: str = None, started_at: float = None):
    with conn_ctx() as conn:
        conn.execute("""
            INSERT INTO crawl_log
                (source_id, source_name, started_at, finished_at,
                 articles_found, articles_new, status, error)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?)
        """, (source_id, source_name,
              started_at, found, new, status, error))


# ============ 查询 ============

def list_news(limit=50, offset=0, country_id=None, player_id=None,
              source_name=None, query=None, include_content=False,
              exclude_video=True):
    """列表查询，支持国家/球员/源/全文搜索筛选"""
    content_field = "n.content," if include_content else ""
    sql = f"""
        SELECT n.id, n.url, n.title, n.summary, {content_field} n.source_name,
               n.language, n.published_at, n.image_url, n.crawled_at,
               GROUP_CONCAT(DISTINCT c.name_cn) AS countries,
               GROUP_CONCAT(DISTINCT p.name_cn) AS players
        FROM news n
        LEFT JOIN news_country_links ncl ON ncl.news_id = n.id
        LEFT JOIN countries c ON c.id = ncl.country_id
        LEFT JOIN news_player_links npl ON npl.news_id = n.id
        LEFT JOIN players p ON p.id = npl.player_id
    """
    where = []
    params = []
    # 过滤视频内容（集锦、highlights、mp4缩略图）
    if exclude_video:
        where.append("""n.title NOT LIKE '%集锦%'
            AND n.title NOT LIKE '%视频%'
            AND n.title NOT LIKE '%highlights%'
            AND n.title NOT LIKE '%highlight%'
            AND (n.image_url IS NULL OR n.image_url NOT LIKE '%.mp4%')
            AND (n.image_url IS NULL OR n.image_url NOT LIKE '%smart.mp4%')
        """)
    if country_id:
        where.append("n.id IN (SELECT news_id FROM news_country_links WHERE country_id=?)")
        params.append(country_id)
    if player_id:
        where.append("n.id IN (SELECT news_id FROM news_player_links WHERE player_id=?)")
        params.append(player_id)
    if source_name:
        where.append("n.source_name=?")
        params.append(source_name)
    if query:
        # FTS5 全文检索
        where.append("n.id IN (SELECT rowid FROM news_fts WHERE news_fts MATCH ?)")
        params.append(query + "*")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY n.id ORDER BY n.published_at DESC NULLS LAST LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with conn_ctx() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_news_by_id(news_id: int):
    with conn_ctx() as conn:
        row = conn.execute("""
            SELECT n.*, GROUP_CONCAT(DISTINCT c.name_cn) AS countries,
                   GROUP_CONCAT(DISTINCT p.name_cn) AS players
            FROM news n
            LEFT JOIN news_country_links ncl ON ncl.news_id = n.id
            LEFT JOIN countries c ON c.id = ncl.country_id
            LEFT JOIN news_player_links npl ON npl.news_id = n.id
            LEFT JOIN players p ON p.id = npl.player_id
            WHERE n.id=?
            GROUP BY n.id
        """, (news_id,)).fetchone()
        return dict(row) if row else None


def list_countries():
    with conn_ctx() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM countries WHERE qualified=1 ORDER BY name_cn"
        ).fetchall()]


def list_players(country_id=None):
    sql = "SELECT * FROM players"
    params = []
    if country_id:
        sql += " WHERE country_id=?"
        params.append(country_id)
    sql += " ORDER BY name_cn"
    with conn_ctx() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_sources(enabled_only=True):
    sql = """
        SELECT s.*, COUNT(n.id) AS article_count
        FROM sources s LEFT JOIN news n ON n.source_name = s.name
    """
    if enabled_only:
        sql += " WHERE s.enabled=1"
    sql += " GROUP BY s.id ORDER BY article_count DESC, s.tier"
    with conn_ctx() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def get_source_by_name(name: str):
    with conn_ctx() as conn:
        row = conn.execute("SELECT * FROM sources WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None


# ============ 比赛相关（从 match_store.py 注入） ============

def list_upcoming_matches(days=4, only_scheduled=True, from_tomorrow=False):
    """未来比赛。自动过滤已开始（kickoff_at < now）。返回按时间正序。

    from_tomorrow=True: 跳过"今天"（按北京时间），从明天 00:00 开始算 N 天。
    用于定时推送：今天的比赛数据已稳定，没意义。
    """
    sql = """
        SELECT m.*,
               hc.name_cn AS home_cn, hc.name_en AS home_en,
               ac.name_cn AS away_cn, ac.name_en AS away_en,
               (SELECT COUNT(*) FROM news_match_links nml WHERE nml.match_id = m.id) AS article_count
        FROM matches m
        LEFT JOIN countries hc ON hc.id = m.home_country_id
        LEFT JOIN countries ac ON ac.id = m.away_country_id
        WHERE m.kickoff_at > ?
          AND m.kickoff_at <= ?
    """
    if only_scheduled:
        sql += " AND m.status = 'scheduled'"
    sql += " ORDER BY m.kickoff_at"

    # 计算时间窗口（都用 UTC 字符串，与 kickoff_at 存储一致）
    from datetime import datetime, timezone, timedelta
    BJ_TZ = timezone(timedelta(hours=8))
    now_bj = datetime.now(BJ_TZ)
    if from_tomorrow:
        # 北京时间明天 00:00 作为起点
        start_bj = (now_bj + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_bj = now_bj
    end_bj = start_bj + timedelta(days=days) if from_tomorrow else now_bj + timedelta(days=days)
    start_utc = start_bj.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_utc = end_bj.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with conn_ctx() as conn:
        rows = conn.execute(sql, (start_utc, end_utc)).fetchall()
        return [dict(r) for r in rows]


def list_all_matches(limit=100):
    sql = """
        SELECT m.*,
               hc.name_cn AS home_cn, hc.name_en AS home_en,
               ac.name_cn AS away_cn, ac.name_en AS away_en,
               (SELECT COUNT(*) FROM news_match_links nml WHERE nml.match_id = m.id) AS article_count
        FROM matches m
        LEFT JOIN countries hc ON hc.id = m.home_country_id
        LEFT JOIN countries ac ON ac.id = m.away_country_id
        ORDER BY m.kickoff_at DESC
        LIMIT ?
    """
    with conn_ctx() as conn:
        return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]


def get_match(match_id: int):
    sql = """
        SELECT m.*,
               hc.name_cn AS home_cn, hc.name_en AS home_en,
               ac.name_cn AS away_cn, ac.name_en AS away_en
        FROM matches m
        LEFT JOIN countries hc ON hc.id = m.home_country_id
        LEFT JOIN countries ac ON ac.id = m.away_country_id
        WHERE m.id=?
    """
    with conn_ctx() as conn:
        row = conn.execute(sql, (match_id,)).fetchone()
        return dict(row) if row else None


def insert_match_link(news_id: int, match_id: int, tier: str, confidence: float = 1.0):
    with conn_ctx() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO news_match_links (news_id, match_id, tier, confidence)
            VALUES (?, ?, ?, ?)
        """, (news_id, match_id, tier, confidence))


def list_match_news(match_id: int, include_content=False):
    tier_order = "CASE nml.tier WHEN 'A' THEN 1 WHEN 'B' THEN 2 WHEN 'C' THEN 3 WHEN 'D' THEN 4 WHEN 'E' THEN 5 ELSE 9 END"
    content_field = "n.content," if include_content else ""
    sql = f"""
        SELECT n.id, n.url, n.title, {content_field} n.summary, n.source_name,
               n.language, n.published_at, n.image_url, n.crawled_at,
               nml.tier, nml.confidence,
               GROUP_CONCAT(DISTINCT c.name_cn) AS countries,
               GROUP_CONCAT(DISTINCT p.name_cn) AS players
        FROM news_match_links nml
        JOIN news n ON n.id = nml.news_id
        LEFT JOIN news_country_links ncl ON ncl.news_id = n.id
        LEFT JOIN countries c ON c.id = ncl.country_id
        LEFT JOIN news_player_links npl ON npl.news_id = n.id
        LEFT JOIN players p ON p.id = npl.player_id
        WHERE nml.match_id = ?
        AND n.title NOT LIKE '%集锦%'
        AND n.title NOT LIKE '%视频%'
        AND n.title NOT LIKE '%highlights%'
        AND n.title NOT LIKE '%highlight%'
        AND (n.image_url IS NULL OR n.image_url NOT LIKE '%.mp4%')
        GROUP BY n.id
        ORDER BY {tier_order}, n.published_at DESC
    """
    with conn_ctx() as conn:
        return [dict(r) for r in conn.execute(sql, (match_id,)).fetchall()]


def update_match_target(match_id: int, below_target: bool):
    with conn_ctx() as conn:
        conn.execute("""
            UPDATE matches SET below_target=?, last_harvested_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (1 if below_target else 0, match_id))



def count_news(country_id=None, player_id=None, source_name=None, query=None, exclude_video=True):
    """统计新闻总数（不受 limit 限制）"""
    with conn_ctx() as conn:
        sql = "SELECT COUNT(*) FROM news n WHERE 1=1"
        params = []
        if exclude_video:
            sql += """ AND n.title NOT LIKE '%集锦%' AND n.title NOT LIKE '%视频%'
                AND n.title NOT LIKE '%highlights%' AND n.title NOT LIKE '%highlight%'
                AND (n.image_url IS NULL OR n.image_url NOT LIKE '%.mp4%')"""
        if country_id:
            sql += " AND n.id IN (SELECT news_id FROM news_country_links WHERE country_id=?)"
            params.append(country_id)
        if player_id:
            sql += " AND n.id IN (SELECT news_id FROM news_player_links WHERE player_id=?)"
            params.append(player_id)
        if source_name:
            sql += " AND n.source_name=?"
            params.append(source_name)
        if query:
            sql += " AND n.id IN (SELECT rowid FROM news_fts WHERE news_fts MATCH ?)"
            params.append(query + "*")
        return conn.execute(sql, params).fetchone()[0]


def stats():
    """仪表盘统计"""
    with conn_ctx() as conn:
        out = {}
        out["news_total"] = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        out["news_today"] = conn.execute(
            "SELECT COUNT(*) FROM news WHERE crawled_at >= date('now', 'start of day')"
        ).fetchone()[0]
        out["countries"] = conn.execute(
            "SELECT COUNT(*) FROM countries WHERE qualified=1"
        ).fetchone()[0]
        out["players"] = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        out["sources_active"] = conn.execute(
            "SELECT COUNT(*) FROM sources WHERE enabled=1"
        ).fetchone()[0]
        out["by_source"] = [dict(r) for r in conn.execute("""
            SELECT s.display_name, s.name, COUNT(n.id) AS cnt,
                   CASE WHEN s.last_crawled_at IS NOT NULL
                   THEN datetime(s.last_crawled_at, 'unixepoch', '+8 hours')
                   END AS last_crawled_ts
            FROM sources s LEFT JOIN news n ON n.source_name = s.name
            GROUP BY s.id ORDER BY cnt DESC
        """).fetchall()]
        out["recent_logs"] = [dict(r) for r in conn.execute("""
            SELECT *,
                   datetime(started_at, 'unixepoch', '+8 hours') AS started_at_bj
            FROM crawl_log ORDER BY started_at DESC LIMIT 10
        """).fetchall()]
        return out


if __name__ == "__main__":
    # 自检
    print("== stats ==")
    print(json.dumps(stats(), ensure_ascii=False, indent=2))
