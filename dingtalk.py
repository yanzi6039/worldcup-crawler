"""
钉钉群机器人推送
用法：
  from dingtalk import send_summary
  send_summary("世界杯新闻增量", "新增 42 篇，22/30 场达标")
"""
import json
import time
import requests

DINGTALK_WEBHOOK_URL = ""  # 在 config 或环境变量中设置


def set_webhook(url: str):
    global DINGTALK_WEBHOOK_URL
    DINGTALK_WEBHOOK_URL = url


def _send_markdown(title: str, text: str) -> bool:
    """发送 Markdown 消息到钉钉群"""
    if not DINGTALK_WEBHOOK_URL:
        return False
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": text,
        },
    }
    try:
        r = requests.post(DINGTALK_WEBHOOK_URL, json=payload, timeout=10)
        return r.status_code == 200 and r.json().get("errcode") == 0
    except Exception:
        return False


def send_summary(source_stats: dict, match_stats: dict) -> bool:
    """发送增量爬取汇总"""
    total = source_stats.get("total", 0)
    new_today = source_stats.get("new_today", 0)
    sources_detail = source_stats.get("sources", [])
    match_total = match_stats.get("total", 0)
    match_达标 = match_stats.get("达标", 0)
    match_不足 = match_stats.get("不足", 0)

    now = time.strftime("%Y-%m-%d %H:%M")

    lines = [
        f"## ⚽ 世界杯新闻爬虫 · {now}",
        "",
        f"**总文章**: {total} 篇 | **本次新增**: {new_today} 篇",
        "",
        "---",
        "",
        "### 📊 各源产出",
        "",
    ]

    for s in sources_detail:
        lines.append(f"- **{s['name']}**: +{s['new']} 篇")

    lines += [
        "",
        "---",
        "",
        f"### 🏆 比赛覆盖: {match_达标}/{match_total} 场达标",
        "",
    ]

    if match_不足 > 0:
        lines.append(f"⚠ 不足 16 篇: {match_不足} 场（小国配对）")

    lines += [
        "",
        f"[打开网页](http://localhost:8001/matches)",
    ]

    return _send_markdown("世界杯新闻爬虫日报", "\n".join(lines))


def send_alert(msg: str) -> bool:
    """发送告警消息"""
    return _send_markdown("⚠ 爬虫告警", f"## ⚠ 告警\n\n{msg}\n\n{time.strftime('%H:%M:%S')}")
