# World Cup 2026 News Crawler

世界杯新闻爬虫聚合系统，12 个数据源，Web 仪表盘，定时导出 Excel + 钉钉推送。

## 快速开始

```bash
pip install -r requirements.txt
playwright install chromium
python3 run_now.py --init-db
python3 run_now.py --serve
```

浏览器打开 `http://localhost:8001/matches`

## 数据源

| 源 | 方式 | 语言 |
|---|---|---|
| 懂球帝 (API + 球队页) | HTTP | 中文 |
| 直播吧 | HTTP | 中文 |
| ESPN | API | 英文 |
| SofaScore | Playwright | 英文 |
| Squawka | Playwright | 英文 |
| Kickoff | Playwright | 英文 |
| The Analyst (Opta) | HTTP | 英文 |
| FourFourTwo | HTTP | 英文 |
| BBC Sport | RSS | 英文 |
| FIFA | Playwright | 英文 |
| Goal | Playwright | 英文 |
| Rotowire | HTTP | 英文 |

## CLI

```bash
python3 run_now.py --quick              # 快速测试
python3 run_now.py --serve              # 启动 Web + 定时爬虫
python3 run_now.py --refresh-schedule   # 更新赛程
python3 run_now.py --retag-matches      # 重建比赛关联
```

## 定时导出

- 每天 **0:00** 和 **8:00** 自动导出未来 4 天 Excel
- Excel 按日期分组，每场比赛一个 sheet
- 自动推送到 GitHub + 钉钉通知

## 配置

复制 `.env.example` 为 `.env`，填入：

```
GITHUB_TOKEN=ghp_xxx
GITHUB_REPO=username/worldcup-crawler
DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=xxx
```

## 技术栈

Python 3 / FastAPI / SQLite / Playwright / APScheduler / openpyxl
