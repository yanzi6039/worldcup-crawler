"""
SQLite 数据库初始化
跑：python3 db/init_db.py
"""
import os
import sqlite3
import json
import sys

# 项目根
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config import DB_PATH, DATA_DIR, COUNTRIES_JSON, PLAYERS_JSON, SCHEDULE_JSON, SOURCES

SCHEMA = """
-- 数据源
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT,
    tier INTEGER,
    language TEXT,
    base_url TEXT,
    enabled INTEGER DEFAULT 1,
    last_crawled_at TIMESTAMP,
    crawl_count INTEGER DEFAULT 0
);

-- 国家
CREATE TABLE IF NOT EXISTS countries (
    id INTEGER PRIMARY KEY,
    name_cn TEXT,
    name_en TEXT UNIQUE,
    iso_code TEXT,
    region TEXT,
    qualified INTEGER DEFAULT 0,
    keywords TEXT
);

-- 球员
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    name_cn TEXT,
    name_en TEXT,
    country_id INTEGER REFERENCES countries(id),
    position TEXT,
    keywords TEXT,
    UNIQUE(name_en, country_id)
);
CREATE INDEX IF NOT EXISTS idx_players_country ON players(country_id);

-- 新闻
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    url_hash TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    title_hash TEXT,
    signature TEXT,
    summary TEXT,
    content TEXT,
    source_id INTEGER REFERENCES sources(id),
    source_name TEXT,
    author TEXT,
    published_at TIMESTAMP,
    crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    language TEXT,
    image_url TEXT,
    is_match_report INTEGER DEFAULT 0,
    related_match_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_source ON news(source_id);
CREATE INDEX IF NOT EXISTS idx_news_url_hash ON news(url_hash);

-- FTS5 全文索引（外部内容表 = news）
CREATE VIRTUAL TABLE IF NOT EXISTS news_fts USING fts5(
    title, content, source_name,
    content='news',
    content_rowid='id',
    tokenize='unicode61'
);

-- 触发器：news 增删改时同步到 FTS
CREATE TRIGGER IF NOT EXISTS news_ai AFTER INSERT ON news BEGIN
    INSERT INTO news_fts(rowid, title, content, source_name)
    VALUES (new.id, new.title, new.content, new.source_name);
END;
CREATE TRIGGER IF NOT EXISTS news_ad AFTER DELETE ON news BEGIN
    INSERT INTO news_fts(news_fts, rowid, title, content, source_name)
    VALUES('delete', old.id, old.title, old.content, old.source_name);
END;
CREATE TRIGGER IF NOT EXISTS news_au AFTER UPDATE ON news BEGIN
    INSERT INTO news_fts(news_fts, rowid, title, content, source_name)
    VALUES('delete', old.id, old.title, old.content, old.source_name);
    INSERT INTO news_fts(rowid, title, content, source_name)
    VALUES (new.id, new.title, new.content, new.source_name);
END;

-- 新闻-国家 关联
CREATE TABLE IF NOT EXISTS news_country_links (
    news_id INTEGER REFERENCES news(id) ON DELETE CASCADE,
    country_id INTEGER REFERENCES countries(id),
    confidence REAL DEFAULT 1.0,
    PRIMARY KEY (news_id, country_id)
);
CREATE INDEX IF NOT EXISTS idx_ncl_country ON news_country_links(country_id);

-- 新闻-球员 关联
CREATE TABLE IF NOT EXISTS news_player_links (
    news_id INTEGER REFERENCES news(id) ON DELETE CASCADE,
    player_id INTEGER REFERENCES players(id),
    confidence REAL DEFAULT 1.0,
    PRIMARY KEY (news_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_npl_player ON news_player_links(player_id);

-- 赛程
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    home_country_id INTEGER REFERENCES countries(id),
    away_country_id INTEGER REFERENCES countries(id),
    kickoff_at TIMESTAMP,
    stage TEXT,
    venue TEXT,
    status TEXT DEFAULT 'scheduled',
    home_score INTEGER,
    away_score INTEGER,
    group_name TEXT,
    match_day INTEGER,
    tournament_round TEXT,
    below_target INTEGER DEFAULT 0,
    last_harvested_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches(kickoff_at);
CREATE INDEX IF NOT EXISTS idx_matches_home ON matches(home_country_id);
CREATE INDEX IF NOT EXISTS idx_matches_away ON matches(away_country_id);

-- 新闻-比赛关联
CREATE TABLE IF NOT EXISTS news_match_links (
    news_id INTEGER REFERENCES news(id) ON DELETE CASCADE,
    match_id INTEGER REFERENCES matches(id) ON DELETE CASCADE,
    tier CHAR(1),                -- A/B/C/D/E（关联强度）
    confidence REAL DEFAULT 1.0,
    PRIMARY KEY (news_id, match_id)
);
CREATE INDEX IF NOT EXISTS idx_nml_match ON news_match_links(match_id);

-- 爬取日志
CREATE TABLE IF NOT EXISTS crawl_log (
    id INTEGER PRIMARY KEY,
    source_id INTEGER,
    source_name TEXT,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    articles_found INTEGER DEFAULT 0,
    articles_new INTEGER DEFAULT 0,
    status TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_clog_started ON crawl_log(started_at DESC);
"""


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    print(f"✓ schema created at {DB_PATH}")

    # 为已存在的 matches 表补字段（兼容旧库）
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(matches)")}
    new_cols = [
        ("group_name", "TEXT"),
        ("match_day", "INTEGER"),
        ("tournament_round", "TEXT"),
        ("below_target", "INTEGER DEFAULT 0"),
        ("last_harvested_at", "TIMESTAMP"),
    ]
    for col, typ in new_cols:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE matches ADD COLUMN {col} {typ}")
            print(f"  + matches.{col} added")
    # news 表补 title_hash / signature 列
    news_cols = {r[1] for r in conn.execute("PRAGMA table_info(news)")}
    for col, typ in [("title_hash", "TEXT"), ("signature", "TEXT")]:
        if col not in news_cols:
            conn.execute(f"ALTER TABLE news ADD COLUMN {col} {typ}")
            print(f"  + news.{col} added")
    conn.commit()

    # 灌入数据源
    for s in SOURCES:
        enabled = 1 if s.get("enabled", True) else 0
        conn.execute("""
            INSERT INTO sources (name, display_name, tier, language, base_url, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                display_name=excluded.display_name,
                tier=excluded.tier,
                language=excluded.language,
                base_url=excluded.base_url,
                enabled=excluded.enabled
        """, (s["name"], s["display_name"], s["tier"], s["language"], s["list_url"], enabled))
    conn.commit()
    print(f"✓ {len(SOURCES)} sources upserted")

    # 灌入国家
    if os.path.exists(COUNTRIES_JSON):
        with open(COUNTRIES_JSON, encoding="utf-8") as f:
            countries = json.load(f)
        for c in countries:
            conn.execute("""
                INSERT INTO countries (name_cn, name_en, iso_code, region, qualified, keywords)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name_en) DO UPDATE SET
                    name_cn=excluded.name_cn,
                    iso_code=excluded.iso_code,
                    region=excluded.region,
                    qualified=excluded.qualified,
                    keywords=excluded.keywords
            """, (c.get("name_cn"), c["name_en"], c.get("iso_code"),
                  c.get("region"), 1 if c.get("qualified") else 0,
                  json.dumps(c.get("keywords", []), ensure_ascii=False)))
        conn.commit()
        print(f"✓ {len(countries)} countries upserted")
    else:
        print(f"⚠ countries.json not found at {COUNTRIES_JSON}, skip")

    # 灌入球员
    if os.path.exists(PLAYERS_JSON):
        with open(PLAYERS_JSON, encoding="utf-8") as f:
            players = json.load(f)
        # 国家名 → id
        cur = conn.execute("SELECT name_en, id FROM countries")
        country_map = {r[0]: r[1] for r in cur.fetchall()}
        count = 0
        # 先清空再灌入（players 是元数据，重新灌不影响 news 关联）
        conn.execute("DELETE FROM players")
        for p in players:
            cid = country_map.get(p.get("country_en"))
            if not cid:
                print(f"  ⚠ country not found: {p.get('country_en')} for {p.get('name_en')}")
                continue
            conn.execute("""
                INSERT INTO players (name_cn, name_en, country_id, position, keywords)
                VALUES (?, ?, ?, ?, ?)
            """, (p.get("name_cn"), p["name_en"], cid, p.get("position"),
                  json.dumps(p.get("keywords", []), ensure_ascii=False)))
            count += 1
        conn.commit()
        print(f"✓ {count} players inserted")
    else:
        print(f"⚠ players.json not found at {PLAYERS_JSON}, skip")

    # 概况
    for tbl in ["sources", "countries", "players", "news", "matches"]:
        cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}")
        print(f"  {tbl}: {cur.fetchone()[0]} rows")

    conn.close()


if __name__ == "__main__":
    init_db()
