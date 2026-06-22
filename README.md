# World Cup 2026 News Crawler

2026 FIFA 世界杯（美加墨）新闻聚合系统。多源爬虫 + 比赛中心挖掘 + Web 仪表盘 + 定时 Excel 导出 + 钉钉推送。

## 架构

```
                  ┌─────────────────────────┐
                  │  赛程层（本地权威）       │
                  │  data/schedule_local.json │  ← 人工校准（北京时间）
                  │  + openfootball JSON     │  ← GitHub 全量 104 场
                  │  + ESPN scoreboard       │  ← 比分实时回填
                  └────────────┬─────────────┘
                               ▼
   多源新闻爬虫  ────►  SQLite (worldcup.db)  ────►  FastAPI Web
   (12 sources)         │   news + matches        │   /matches /news
                        │   news_match_links      │   /dashboard
                        ▼                         ▼
                   match_tagger 关联         APScheduler
                   （5 层 tier A-E）         0:00/8:00 自动
                                            导出 Excel +
                                            GitHub push +
                                            钉钉通知
```

**核心原则**：每场比赛跨源挖 ≥16 篇文章；北京时间统一显示；本地赛程是金标准。

## 快速开始

```bash
pip install -r requirements.txt
playwright install chromium

python3 run_now.py --init-db              # 初始化数据库
python3 run_now.py --rebuild-schedule     # 首次构建赛程（72 场小组赛入库）
python3 run_now.py --serve                # 启动 Web + 定时任务（http://localhost:8001）
```

浏览器打开 `http://localhost:8001/matches`。

## CLI 用法

```bash
# 赛程（2026-06-22 起改用本地+openfootball）
python3 run_now.py --rebuild-schedule     # 清空 matches 表后重建（首次切换）
python3 run_now.py --refresh-schedule     # 增量刷新（保留现有，补+回填比分）

# 新闻爬取
python3 run_now.py --quick                # 每源 5 篇（冒烟）
python3 run_now.py --backfill             # 每源 20 篇（存量）
python3 run_now.py --incremental          # 每源 30 篇（跳过已有）

# 比赛中心
python3 run_now.py --harvest-4d           # 未来 4 天每场挖 16 篇
python3 run_now.py --incremental-3d       # 未来 1-3 天增量挖
python3 run_now.py --harvest-match ID     # 单场挖掘
python3 run_now.py --retag-matches        # 重建比赛的新闻关联

# 服务
python3 run_now.py --serve                # 启 Web + APScheduler
```

## 赛程数据源（2026-06-22 升级）

旧版用 Wikipedia fixture box 解析，时区处理脆弱。新版三层数据流：

| 源 | 角色 | URL | 说明 |
|---|---|---|---|
| `data/schedule_local.json` | 权威种子 | 本地 | 人工校准的 19 场（北京时间），任何冲突以此为准 |
| `openfootball/worldcup.json` | 全量补完 | `raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json` | 104 场全量赛程，UTC 偏移转 UTC 存储 |
| ESPN scoreboard API | 比分回填 | `site.api.espn.com/.../fifa.world/scoreboard?dates=2026` | 每小时同步状态/比分，不新建比赛 |

**存储约定**：`matches.kickoff_at` 一律 UTC（`YYYY-MM-DD HH:MM:SS` 无后缀），显示层 `web/timezone_utils.to_beijing()` 自动 +8 转北京时间。

**合并去重 key**：`(home_country_id, away_country_id, date(kickoff_at))`。本地数据优先，其他源仅补比分。

**剔除规则**：淘汰赛占位符（"1A/2B"、"W76/W78" 等）跳过，等小组赛结束再补对阵。

## 新闻数据源

| 源 | 方式 | 语言 |
|---|---|---|
| ESPN | API | 英文 |
| FourFourTwo | RSS | 英文 |
| BBC Sport | RSS | 英文 |
| The Analyst (Opta) | HTTP | 英文 |
| Rotowire | HTTP | 英文 |
| Kickoff | Playwright | 英文 |
| Squawka | Playwright | 英文 |
| SofaScore | Playwright | 英文 |
| Goal | Playwright | 英文 |
| FlashScore | curl_cffi | 英文 |

> 注：懂球帝 / 直播吧等中文数据源的爬虫代码因版权原因已从公开仓库排除（见 `.gitignore`），仅本地保留。

## Web 页面

- `/` Dashboard：数据源统计 + 实时爬取日志
- `/matches` 比赛中心：未来 4 天比赛列表 + 每场文章进度条（目标 16 篇）+ 一键挖掘按钮
- `/news` 新闻列表：按国家/球员/源/关键词筛选
- `/news/{id}` 新闻详情：正文 + 关联标签
- `/api/matches` JSON API
- `/api/matches/{id}/news` 单场新闻 JSON
- `/api/matches/{id}/export.ndjson` NDJSON 流式导出

## 定时任务（APScheduler）

| 时间 | 任务 |
|---|---|
| 每小时 :00 | ESPN 比分回填 + 增量爬虫 |
| 每日 0:00 / 8:00 | 导出未来 4 天 Excel → push GitHub → 钉钉通知 |
| 每日 3:17 | 清理 90min 源的旧 2025 文章 |

## 配置

复制 `.env.example` 为 `.env`：

```
GITHUB_TOKEN=ghp_xxx
GITHUB_REPO=username/worldcup-crawler
DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=xxx
```

## 文件结构

```
worldcup-crawler/
├── run_now.py                    # ★ CLI 入口（强制 TZ=Asia/Shanghai）
├── config.py                     # 数据源 + UA 池 + 路径
├── base_scraper.py               # 三档抓取（requests/playwright/curl_cffi）
├── tagger.py                     # 关键词打标（国家/球员）
├── match_tagger.py               # 比赛-新闻关联（5 层 tier）
├── match_keywords.py             # 单场比赛关键词生成
│
├── scrapers/
│   ├── schedule_builder.py       # ★ 新赛程入口（本地+openfootball+ESPN）
│   ├── match_harvester.py        # Google News RSS 单场挖掘
│   ├── tier1_easy.py             # Tier 1 源聚合
│   └── ...                       # 各源爬虫
│
├── db/
│   ├── init_db.py                # schema 定义 + 数据灌入
│   ├── store.py                  # 读写接口
│   └── worldcup.db               # ★ SQLite 单文件
│
├── web/
│   ├── app.py                    # FastAPI 路由
│   ├── timezone_utils.py         # UTC → 北京时间
│   └── templates/                # Jinja2 模板
│
├── data/
│   ├── schedule_local.json       # ★ 人工校准权威赛程（北京时间）
│   ├── schedule.json             # 合并后的完整赛程备份
│   ├── countries.json / players.json
│   └── *.xlsx                    # 导出文件（按日期）
│
└── .env                          # 密钥（不提交）
```

## 技术栈

Python 3 / FastAPI / SQLite (FTS5) / Playwright / APScheduler / openpyxl / Jinja2 / Google News RSS / curl_cffi

## 免责声明

本项目仅作技术学习与个人使用。所有新闻版权归原作者所有，数据源若涉及版权请联系移除。赛程数据来自 openfootball GitHub 开源项目与 ESPN 公开 API。
