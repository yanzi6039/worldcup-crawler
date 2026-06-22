"""
db/store.py 扩展：比赛相关查询
"""
# 这些函数会被 monkey-patch 到 store 模块
# 通过 from db import store; store.list_upcoming_matches(...)


def list_upcoming_matches(days=4, only_scheduled=True):
    """列出未来 N 天的比赛"""
    import sqlite3
    from db.store import conn_ctx
    sql = """
        SELECT m.*,
               hc.name_cn AS home_cn, hc.name_en AS home_en,
               ac.name_cn AS away_cn, ac.name_en AS away_en,
               (SELECT COUNT(*) FROM news_match_links nml WHERE nml.match_id = m.id) AS article_count
        FROM matches m
        LEFT JOIN countries hc ON hc.id = m.home_country_id
        LEFT JOIN countries ac ON ac.id = m.away_country_id
        WHERE m.kickoff_at >= datetime('now')
          AND m.kickoff_at <= datetime('now', ?)
    """
    if only_scheduled:
        sql += " AND m.status = 'scheduled'"
    sql += " ORDER BY m.kickoff_at"
    with conn_ctx() as conn:
        rows = conn.execute(sql, (f"+{days} days",)).fetchall()
        return [dict(r) for r in rows]


def list_all_matches(limit=100):
    """所有比赛（含已完赛）"""
    from db.store import conn_ctx
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
    from db.store import conn_ctx
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
    """关联新闻-比赛"""
    from db.store import conn_ctx
    with conn_ctx() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO news_match_links (news_id, match_id, tier, confidence)
            VALUES (?, ?, ?, ?)
        """, (news_id, match_id, tier, confidence))


def list_match_news(match_id: int, min_tier: str = None, include_content=False):
    """该比赛关联的新闻（按 tier 优先）"""
    from db.store import conn_ctx
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
        GROUP BY n.id
        ORDER BY {tier_order}, n.published_at DESC
    """
    with conn_ctx() as conn:
        return [dict(r) for r in conn.execute(sql, (match_id,)).fetchall()]


def update_match_target(match_id: int, below_target: bool):
    from db.store import conn_ctx
    with conn_ctx() as conn:
        conn.execute("""
            UPDATE matches SET below_target=?, last_harvested_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (1 if below_target else 0, match_id))
