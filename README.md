# 世界杯新闻爬虫聚合 · 2026 美加墨

> 从 12 个数据源每日爬取世界杯相关新闻、对战分析；FastAPI 极简单页可点击拉取；按国家和球星筛选。

## 快速开始

### 1. 初始化数据库

```bash
cd /Users/john/claude/worldcup-crawler
python3 db/init_db.py
```

会建好 SQLite 表 + 灌入 12 个数据源、47 国、65+ 球星。

### 2. 跑一次爬虫

```bash
# 快速：每源 5 篇（约 30 秒）
python3 run_now.py --quick

# 存量：每源 20 篇
python3 run_now.py --backfill

# 增量：每源 30 篇，跳过已有
python3 run_now.py --incremental

# 全量：每源 50 篇
python3 run_now.py --full

# 指定源
python3 run_now.py --source=fourfourtwo,espn --quick
```

### 3. 启动 Web 页面

```bash
# 只起 Web（手动点按钮触发爬取）
python3 web/app.py
# 浏览器开 http://localhost:8000

# 或：Web + 定时爬虫（24/7 模式）
python3 run_now.py --serve
```

打开 http://localhost:8000：
- 顶部按 **国家 / 球星 / 来源 / 关键词** 筛选
- 右上角「🔄 拉取最新」按钮触发后台爬取
- `/dashboard` 看爬虫状态和各源新闻数

## 数据源

| Tier | 源 | 策略 | 状态 |
|------|----|----|------|
| 🟢 1 | FourFourTwo | RSS | ✅ 已验证 |
| 🟢 1 | ESPN Soccer | RSS | ✅ 已验证 |
| 🟢 1 | FIFA 官网 | 待 Playwright | ⏳ v1 |
| 🟡 2 | 懂球帝 / 直播吧 / Squawka / SofaScore / Kickoff | Playwright + 反爬 | ⏳ v1 |
| 🔴 3 | Goal / Opta / FlashScore | curl_cffi + 代理 | ⏳ v1 |

## 数据模型

`db/worldcup.db`（SQLite，单文件）：
- `news` + `news_fts`（FTS5 全文索引）
- `countries`（47 国，含中英文关键词）
- `players`（65+ 球星，每国关键球员）
- `news_country_links` / `news_player_links`（新闻→国家/球员多对多）
- `crawl_log`（爬取日志）

## 关键技术

- **RSS 优先**：FourFourTwo、ESPN 都用 RSS（XML 解析），比 HTML 爬简单 10 倍
- **反爬三件套**：UA 轮换（10 个真实 Chrome UA）+ 随机延迟（1-3s）+ 指数退避
- **去重**：URL MD5 哈希，upsert 跳过已有
- **打标**：关键词匹配（国家用标题，球员用标题+正文前 3000 字）
- **全文搜索**：FTS5 触发器自动同步 news → news_fts

## 目录结构

```
worldcup-crawler/
├── run_now.py            # ★ CLI 入口（--quick/--backfill/--incremental/--serve）
├── config.py             # 12 源 + UA 池 + 节流参数
├── base_scraper.py       # 基类：重试/退避/UA轮换/三档抓取
├── tagger.py             # 国家/球员打标
├── db/
│   ├── init_db.py        # ★ 建库脚本
│   ├── store.py          # 读写封装
│   └── worldcup.db       # SQLite 文件
├── scrapers/
│   └── tier1_easy.py     # FFT / ESPN（RSS）+ Rotowire（HTML）
├── web/
│   ├── app.py            # ★ FastAPI
│   └── templates/        # Jinja2 模板
└── data/
    ├── countries.json
    └── players.json
```

## 当前数据

（2026-06-21 跑出的）

- 22 篇真新闻（FFT 16 + ESPN 6）
- 真实赛事：Spain vs Saudi Arabia / Tunisia vs Japan / Ecuador vs Curacao / Germany vs Ivory Coast
- 16 条国家标签 + 25 条球员标签

## 待办（v1）

- [ ] FIFA 官网（需要 Playwright）
- [ ] Tier 2 五个源（懂球帝等）
- [ ] Tier 3 困难源（Goal/ESPN CF 严防）
- [ ] 大模型实体识别替代关键词打标
- [ ] 赛程自动关联（一篇战报自动绑对应 match）
- [ ] 代理池
