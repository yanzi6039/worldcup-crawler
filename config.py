"""
世界杯新闻爬虫 - 配置中心
12 个数据源、UA 池、请求间隔、路径
"""
import os

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# SQLite 数据库路径
DB_PATH = os.path.join(BASE_DIR, "db", "worldcup.db")

# 数据 JSON
DATA_DIR = os.path.join(BASE_DIR, "data")
COUNTRIES_JSON = os.path.join(DATA_DIR, "countries.json")
PLAYERS_JSON = os.path.join(DATA_DIR, "players.json")
SCHEDULE_JSON = os.path.join(DATA_DIR, "schedule.json")

# 日志
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "crawl.log")

# Web（避开 codex 占用的 8000）
WEB_HOST = "0.0.0.0"
WEB_PORT = 8001

# 钉钉群机器人 Webhook URL（你的群机器人地址）
DINGTALK_WEBHOOK_URL = ""

# ============ 请求节流 ============
REQUEST_DELAY_MIN = 1.0       # 同源请求最小间隔（秒）
REQUEST_DELAY_MAX = 3.0
RETRY_MAX = 3
RETRY_BACKOFF_BASE = 10       # 第 N 次重试等待 N*10 秒
PAGE_TIMEOUT = 20             # 单页超时（秒）

# ============ 新闻时效 ============
# 超过 N 天的新闻视为过期，停止本源爬取（RSS 通常按时间倒序，遇到老文章可 break）
NEWS_MAX_AGE_DAYS = 3

# ============ UA 池（10 个真实 Chrome UA） ============
UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

# Accept-Language 池
ACCEPT_LANGUAGE_POOL = [
    "zh-CN,zh;q=0.9,en;q=0.8",
    "en-US,en;q=0.9",
    "zh-CN,zh;q=0.9",
    "en-US,en;q=0.5",
]

# ============ 12 个数据源 ============
# tier: 1=requests 友好，2=playwright 中等，3=curl_cffi 困难
SOURCES = [
    # ---- Tier 1 友好 ----
    {
        "name": "fifa",
        "display_name": "FIFA 官网",
        "tier": 1,
        "language": "en",
        "list_url": "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026",
        "article_selector": "a[href*='/articles/']",
        "enabled": False,  # SPA JS 渲染，需 Playwright，v1 启用
    },
    {
        "name": "fourfourtwo",
        "display_name": "FourFourTwo",
        "tier": 1,
        "language": "en",
        "list_url": "https://www.fourfourtwo.com/world-cup",
        "article_selector": "a[href*='/news/']",
    },
    {
        "name": "rotowire",
        "display_name": "Rotowire",
        "tier": 1,
        "language": "en",
        "list_url": "https://www.rotowire.com/soccer/news.php?tournament=world-cup",
        "article_selector": "a.news-link",
        "enabled": False,  # HTML JS 渲染，不是新闻源
    },

    # ---- Playwright 源（SPA / 反爬） ----
    {"name": "squawka", "display_name": "Squawka", "tier": 2, "language": "en",
     "list_url": "https://www.squawka.com/en/news/"},
    {"name": "fifa", "display_name": "FIFA 官网", "tier": 2, "language": "en",
     "list_url": "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/teams/argentina/team-news"},
    {"name": "sofascore", "display_name": "SofaScore", "tier": 3, "language": "en",
     "list_url": "https://www.sofascore.com/api/v1/sofascore-news/en/category/world-cup/posts/1"},
    {"name": "dongqiudi", "display_name": "懂球帝", "tier": 1, "language": "zh",
     "list_url": "https://www.dongqiudi.com/api/app/tabs/web/253.json"},
    {"name": "goal_pw", "display_name": "Goal.com (Playwright)", "tier": 2, "language": "en",
     "list_url": "https://www.goal.com/en/world-cup/news/70excpe1synn9kadnbppahdn7"},
    {"name": "espn_pw", "display_name": "ESPN (Playwright)", "tier": 2, "language": "en",
     "list_url": "https://www.espn.com/soccer/teams/_/league/fifa.world"},
    {"name": "flashscore_pw", "display_name": "FlashScore (Playwright)", "tier": 2, "language": "en",
     "list_url": "https://www.flashscore.com/news/football/"},
    {"name": "kickoff_pw", "display_name": "Kickoff (Playwright)", "tier": 2, "language": "en",
     "list_url": "https://kickoff.guide/blog/tag/worldcup-2026"},

    # ---- 按站点定制的爬虫（HTTP 友好） ----
    {
        "name": "kickoff",
        "display_name": "Kickoff Guide",
        "tier": 1,
        "language": "en",
        "list_url": "https://kickoff.guide/blog/tag/worldcup-2026",
    },
    {
        "name": "theanalyst",
        "display_name": "The Analyst (Opta)",
        "tier": 1,
        "language": "en",
        "list_url": "https://theanalyst.com/competition/fifa-world-cup/articles/",
    },
    {
        "name": "goal",
        "display_name": "Goal.com",
        "tier": 1,
        "language": "en",
        "list_url": "https://www.goal.com/en/world-cup/news/70excpe1synn9kadnbppahdn7",
    },
    {
        "name": "flashscore",
        "display_name": "FlashScore",
        "tier": 1,
        "language": "en",
        "list_url": "https://www.flashscore.com/news/football/",
    },
    {
        "name": "rotowire",
        "display_name": "Rotowire",
        "tier": 1,
        "language": "en",
        "list_url": "https://www.rotowire.com/soccer/news.php?competition=1",
    },

    # ---- Google News（搜索聚合，match 专用）----
    {
        "name": "google_news",
        "display_name": "Google News",
        "tier": 1,
        "language": "en",
        "list_url": "https://news.google.com/rss/search?q=World+Cup+2026",
    },

    # ---- 通用 RSS 源（开放、稳定、易解析）----
    {
        "name": "bbc_sport",
        "display_name": "BBC Sport",
        "tier": 1,
        "language": "en",
        "list_url": "https://feeds.bbci.co.uk/sport/football/rss.xml",
    },
    {
        "name": "guardian",
        "display_name": "The Guardian",
        "tier": 1,
        "language": "en",
        "list_url": "https://www.theguardian.com/football/rss",
    },
    {
        "name": "skysports",
        "display_name": "Sky Sports",
        "tier": 1,
        "language": "en",
        "list_url": "https://www.skysports.com/rss/12040",
    },
    {
        "name": "90min",
        "display_name": "90min",
        "tier": 1,
        "language": "en",
        "list_url": "https://www.90min.com/posts.rss",
    },

    # ---- Tier 2 中等 ----
    {
        "name": "dongqiudi",
        "display_name": "懂球帝",
        "tier": 2,
        "language": "zh",
        "list_url": "https://www.dongqiudi.com/team/50001566",  # 世界杯专题（占位，实际跑时校准）
        "article_selector": "a[href*='/article/']",
    },
    {
        "name": "zhibo8",
        "display_name": "直播吧",
        "tier": 2,
        "language": "zh",
        "list_url": "https://www.zhibo8.cc/soccer/",
        "article_selector": "a[href*='/news/']",
    },
    {
        "name": "squawka",
        "display_name": "Squawka",
        "tier": 2,
        "language": "en",
        "list_url": "https://www.squawka.com/en/news/world-cup/",
        "article_selector": "a[href*='/en/news/']",
    },
    {
        "name": "sofascore",
        "display_name": "SofaScore",
        "tier": 2,
        "language": "en",
        "list_url": "https://www.sofascore.com/tournament/football/world-cup/16",
        "article_selector": "a[href*='/news/']",
    },
    {
        "name": "kickoff",
        "display_name": "Kickoff",
        "tier": 2,
        "language": "en",
        "list_url": "https://www.kickoff.com/news/",
        "article_selector": "a.news-link",
    },

    # ---- Tier 3 困难（v1 启用） ----
    {
        "name": "goal",
        "display_name": "Goal.com",
        "tier": 3,
        "language": "en",
        "list_url": "https://www.goal.com/en/news/world-cup",
        "article_selector": "a[href*='/en/news/']",
        "enabled": False,  # v1 启用
    },
    {
        "name": "espn",
        "display_name": "ESPN",
        "tier": 1,
        "language": "en",
        "list_url": "https://www.espn.com/espn/rss/soccer/news",
        "article_selector": "a[href*='/story/']",
    },
    {
        "name": "opta",
        "display_name": "Opta (The Analyst)",
        "tier": 3,
        "language": "en",
        "list_url": "https://theanalyst.com/eu/2025/01/world-cup-2026/",
        "article_selector": "a[href*='/eu/']",
        "enabled": False,
    },
    {
        "name": "flashscore",
        "display_name": "FlashScore",
        "tier": 3,
        "language": "en",
        "list_url": "https://www.flashscore.com/football/world-cup/",
        "article_selector": "a[href*='/news/']",
        "enabled": False,
    },
]

# 按 name 索引，方便查
SOURCES_BY_NAME = {s["name"]: s for s in SOURCES}

# ============ 世界杯常量 ============
WC_2026_START = "2026-06-11"
WC_2026_END = "2026-07-19"
WC_HOSTS = ["USA", "Canada", "Mexico"]
WC_TEAM_COUNT = 48
