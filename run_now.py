"""
世界杯新闻爬虫 · CLI 入口

用法：
  python3 run_now.py --quick           # 快速测试：每源 5 篇
  python3 run_now.py --backfill        # 存量：每源 20 篇
  python3 run_now.py --incremental     # 增量：跳过已有，每源 30 篇
  python3 run_now.py --source=fifa,espn
  python3 run_now.py --serve           # 启动 Web + 后台定时爬虫（24/7）
  python3 run_now.py --init-db         # 重建数据库
"""
import os
import sys
import argparse
import time
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import LOG_DIR, WEB_PORT
from db import store
from scrapers.tier1_easy import TIER1_SCRAPERS

# 收集所有可用爬虫（v1 加 TIER2_SCRAPERS 等）
ALL_SCRAPERS = {**TIER1_SCRAPERS}

log = logging.getLogger("runner")


def setup_logging(verbose=False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(LOG_DIR, "crawl.log"),
                                encoding="utf-8"),
        ],
    )
    os.makedirs(LOG_DIR, exist_ok=True)


def run_crawl(sources: list[str], max_per_source: int, skip_existing: bool = True):
    """同步跑一次爬虫"""
    started = time.time()
    log.info(f"▶▶ 启动爬取: sources={sources or 'all'}, max={max_per_source}, skip_existing={skip_existing}")

    target_classes = []
    for name, cls in ALL_SCRAPERS.items():
        if sources and name not in sources:
            continue
        src = store.get_source_by_name(name)
        if not src or not src["enabled"]:
            log.info(f"  跳过 {name} (disabled)")
            continue
        target_classes.append((name, cls))

    if not target_classes:
        log.error("没有可用爬虫")
        return

    total_found = 0
    total_new = 0
    for name, cls in target_classes:
        log.info(f"  → {name}")
        try:
            s = cls()
            found, new = s.crawl(max_articles=max_per_source, skip_existing=skip_existing)
            total_found += found
            total_new += new
        except Exception as e:
            log.exception(f"  ✗ {name} error: {e}")

    # 关 playwright
    try:
        from base_scraper import close_browser
        close_browser()
    except Exception:
        pass

    log.info(f"✓✓ 完成: 共 {len(target_classes)} 源, found={total_found}, new={total_new}, "
             f"耗时 {time.time()-started:.1f}s")


def _start_public_tunnel(port: int):
    """启动 cloudflared 隧道获得公网 URL（免费，无需注册）"""
    import subprocess
    import threading
    import re

    tunnel_url = [None]

    def _run():
        try:
            proc = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for line in proc.stdout:
                line = line.strip()
                if "trycloudflare.com" in line:
                    m = re.search(r'https://[^\s]+\.trycloudflare\.com', line)
                    if m:
                        tunnel_url[0] = m.group(0)
                        log.info(f"🌍 公网地址: {tunnel_url[0]}")
                        log.info(f"   比赛页面: {tunnel_url[0]}/matches")
                        log.info(f"   新闻列表: {tunnel_url[0]}/")
                        # 写文件方便查看
                        with open(os.path.join(BASE_DIR, "tunnel_url.txt"), "w") as f:
                            f.write(f"{tunnel_url[0]}\n{tunnel_url[0]}/matches\n")
                if "failed" in line.lower() or "error" in line.lower():
                    if "trycloudflare" not in line:  # 忽略 "failed to get tunnel" 等临时错误
                        log.warning(f"  cloudflared: {line}")
        except FileNotFoundError:
            log.info("  cloudflared 未安装 (brew install cloudflared)，跳过公网隧道")
        except Exception as e:
            log.info(f"  cloudflared error: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return tunnel_url


def serve_mode():
    """启动 Web + 后台持续爬虫 + 公网隧道（24/7）"""
    log.info("🌐 启动 Web 服务 + 后台持续爬虫")
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from web.app import app
        import uvicorn

        scheduler = BackgroundScheduler()

        # 每 5 分钟检查一次，随机决定是否跑各 tier
        scheduler.add_job(
            _random_crawl_cycle,
            "interval", minutes=5, id="random_crawl",
        )
        # 每天凌晨 3 点清理 3 天前旧数据
        scheduler.add_job(
            lambda: _cleanup_old_articles(3),
            "cron", hour=3, minute=17, id="cleanup",
        )
        # 定时导出：每天 8:00 和 0:00
        scheduler.add_job(
            _export_and_push, "cron", hour=0, minute=7, id="export_midnight",
        )
        scheduler.add_job(
            _export_and_push, "cron", hour=8, minute=7, id="export_morning",
        )
        scheduler.start()
        log.info("✓ 随机爬虫已启动：API每30-60m / HTTP每1-2h / PW每2-4h")
        log.info("✓ 定时导出：每天 0:00 / 8:00")

        # 公网隧道（pyngrok，办公电脑用它访问）
        tunnel_url = _start_public_tunnel(WEB_PORT)

        log.info(f"💻 本地: http://localhost:{WEB_PORT}/matches")
        log.info(f"💻 本地: http://localhost:{WEB_PORT} (新闻列表)")

        # Web 用 8001 端口
        uvicorn.run(app, host="0.0.0.0", port=WEB_PORT)
    except KeyboardInterrupt:
        log.info("👋 退出")
    finally:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass


def _send_dingtalk_summary():
    """发送钉钉汇总"""
    try:
        from db import store
        from dingtalk import set_webhook, send_summary
        from config import DINGTALK_WEBHOOK_URL

        if not DINGTALK_WEBHOOK_URL:
            return
        set_webhook(DINGTALK_WEBHOOK_URL)

        stats = store.stats()
        sources = store.list_sources(enabled_only=False)
        source_detail = []
        for s in sources:
            cnt = s.get("article_count", 0) or 0
            if cnt > 0:
                source_detail.append({"name": s["display_name"] or s["name"], "new": cnt})
        source_detail.sort(key=lambda x: x["new"], reverse=True)

        matches = store.list_upcoming_matches(days=7, only_scheduled=False)
        total_m = len(matches)
        ok_m = sum(1 for m in matches if (m.get("article_count", 0) or 0) >= 16)
        low_m = total_m - ok_m

        send_summary({
            "total": stats.get("news_total", 0),
            "new_today": stats.get("news_today", 0),
            "sources": source_detail[:10],
        }, {
            "total": total_m,
            "达标": ok_m,
            "不足": low_m,
        })
    except Exception as e:
        log.warning(f"dingtalk summary error: {e}")


def _crawl_one_source(name: str, max_per: int):
    """爬单个源（给线程池调用）"""
    from scrapers.tier1_easy import TIER1_SCRAPERS
    from scrapers.playwright_sources import PLAYWRIGHT_SCRAPERS
    from scrapers.per_source import PER_SOURCE_SCRAPERS
    from web.crawl_status import crawl_status

    all_scrapers = {**TIER1_SCRAPERS, **PLAYWRIGHT_SCRAPERS, **PER_SOURCE_SCRAPERS}
    cls = all_scrapers.get(name)
    if not cls:
        return
    src = store.get_source_by_name(name)
    if not src or not src["enabled"]:
        return
    crawl_status.source_start(name)
    try:
        s = cls()
        found, new = s.crawl(max_articles=max_per, skip_existing=True)
        crawl_status.source_done(name, found, new, True)
        crawl_status.log_event(f"  ✓ {name}: 找到{found} 新增{new}")
    except Exception as e:
        crawl_status.source_done(name, 0, 0, False)
        crawl_status.log_event(f"  ✗ {name}: {e}", "error")


def _crawl_tier(tier_name: str, source_names: list[str], max_per: int):
    """并发爬指定 tier 的源（最多 3 个同时）"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from web.crawl_status import crawl_status

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_crawl_one_source, name, max_per): name for name in source_names}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:
                name = futures[fut]
                crawl_status.log_event(f"  ✗ {name}: {e}", "error")


def _random_crawl_cycle():
    """随机的全源爬取（不同 tier 不同间隔）"""
    import random as _rand
    from match_tagger import tag_all_upcoming
    from web.crawl_status import crawl_status

    if crawl_status.is_running():
        return

    crawl_status.start_crawl("")
    crawl_status.log_event(f"🔄 定时爬取启动 {time.strftime('%H:%M')}")

    # Tier 定义
    tier_sources = {
        "API": ["dongqiudi", "espn_pw", "theanalyst_api", "bbc_sport", "skysports", "90min", "guardian"],
        "HTTP": ["theanalyst", "flashscore", "goal", "fourfourtwo", "rotowire", "kickoff"],
        "PW": ["sofascore", "squawka", "fifa", "espn", "goal_pw"],
    }

    now_min = int(time.strftime("%M")) + int(time.strftime("%H")) * 60
    any_ran = False

    for tier, sources in tier_sources.items():
        if tier == "API":
            if now_min % _rand.randint(30, 60) > _rand.randint(5, 15):
                continue
            max_per = 20
        elif tier == "HTTP":
            if now_min % _rand.randint(60, 120) > _rand.randint(10, 25):
                continue
            max_per = 15
        else:
            if now_min % _rand.randint(120, 240) > _rand.randint(15, 30):
                continue
            max_per = 10

        crawl_status.log_event(f"▶ T{tier} 启动，{len(sources)} 个源")
        _crawl_tier(tier, sources, max_per)
        any_ran = True

    # 重打标签
    try:
        if any_ran:
            crawl_status.log_event("  🏷 更新比赛标签…")
            tag_all_upcoming(days=7)
            # 打印比赛覆盖
            matches = store.list_upcoming_matches(days=4, only_scheduled=False)
            ok = sum(1 for m in matches if (m.get("article_count", 0) or 0) >= 16)
            crawl_status.log_event(f"  比赛覆盖: {ok}/{len(matches)} 场达标")
    except: pass

    try:
        from base_scraper import close_browser
        close_browser()
    except: pass

    crawl_status.log_event(f"✓ 定时爬取完成 | 新增 {crawl_status.status().get('total_new', 0)} 篇")
    crawl_status.finish_crawl()


def _continuous_crawl_cycle():
    """后备：兜底全源爬取 + 重打标签"""
    from scrapers.match_harvester import run_all_sources
    from match_tagger import tag_all_upcoming
    try:
        run_all_sources(max_per_source=20)
        tag_all_upcoming(days=7)
    except Exception as e:
        log.exception(f"continuous cycle error: {e}")


def _cleanup_old_articles(days: int = 3):
    """清理 N 天前的旧文章"""
    import sqlite3
    cutoff = (f"{(time.strftime('%Y-%m-%d'))}" )  # today
    try:
        conn = sqlite3.connect(os.path.join(BASE_DIR, "db", "worldcup.db"))
        deleted = conn.execute(f"""
            DELETE FROM news WHERE published_at < date('now', '-{days} days')
               OR (published_at IS NULL AND crawled_at < datetime('now', '-{days} days'))
        """).rowcount
        conn.commit()
        conn.close()
        if deleted > 0:
            log.info(f"🧹 清理了 {deleted} 篇 {days} 天前旧文章")
    except Exception as e:
        log.warning(f"cleanup error: {e}")


def _load_env():
    """加载 .env 环境变量"""
    env_file = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def _export_and_push():
    """导出 4 天 Excel → push 到 GitHub → 钉钉通知"""
    import subprocess
    import io as _io

    log.info("📊 定时导出 Excel...")
    _load_env()

    try:
        # 生成 Excel
        from db import store
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment

        matches = store.list_upcoming_matches(days=4, only_scheduled=False)
        news = store.list_news(limit=5000, exclude_video=True)

        wb = Workbook()

        # Sheet 1: 比赛
        ws1 = wb.active
        ws1.title = "Matches"
        ws1.append(["Match ID", "Home", "Away", "Group", "Stage", "Kickoff (BJ)", "Articles", "Status"])
        for m in matches:
            ws1.append([
                m["id"],
                f"{m.get('home_cn','') or m.get('home_en','')}",
                f"{m.get('away_cn','') or m.get('away_en','')}",
                m.get("group_name", ""),
                m.get("stage", ""),
                (m.get("kickoff_at") or "")[:16],
                m.get("article_count", 0) or 0,
                "✓" if (m.get("article_count", 0) or 0) >= 16 else "⚠",
            ])
        for cell in ws1[1]:
            cell.font = Font(bold=True)

        # Sheet 2: 新闻
        ws2 = wb.create_sheet("News")
        ws2.append(["ID", "Title", "Source", "Published", "Language", "URL", "Summary"])
        for n in news:
            ws2.append([
                n.get("id"),
                n.get("title", ""),
                n.get("source_name", ""),
                (n.get("published_at") or "")[:19],
                n.get("language", ""),
                n.get("url", ""),
                (n.get("summary") or "")[:200],
            ])
        for cell in ws2[1]:
            cell.font = Font(bold=True)

        # 保存
        export_dir = os.path.join(BASE_DIR, "data")
        os.makedirs(export_dir, exist_ok=True)
        xlsx_path = os.path.join(export_dir, f"worldcup_4d_{time.strftime('%m%d_%H%M')}.xlsx")
        latest_path = os.path.join(export_dir, "worldcup_latest.xlsx")
        wb.save(xlsx_path)
        wb.save(latest_path)
        log.info(f"✓ Excel 已生成: {xlsx_path}")

        # Push 到 GitHub
        token = os.environ.get("GITHUB_TOKEN", "")
        repo = os.environ.get("GITHUB_REPO", "")

        if token and repo:
            # 确保 data 目录有 gitkeep
            gitkeep = os.path.join(export_dir, ".gitkeep")
            if not os.path.exists(gitkeep):
                with open(gitkeep, "w") as f:
                    pass

            subprocess.run(["git", "-C", BASE_DIR, "add", "data/"], capture_output=True)
            subprocess.run(["git", "-C", BASE_DIR, "commit", "-m",
                           f"Auto export {time.strftime('%m-%d %H:%M')}"], capture_output=True)

            # Push with token
            push_url = f"https://{token}@github.com/{repo}.git"
            r = subprocess.run(["git", "-C", BASE_DIR, "push", push_url, "main"],
                             capture_output=True, text=True)
            if r.returncode == 0:
                log.info("✓ 已推送到 GitHub")
            else:
                log.warning(f"Git push failed: {r.stderr[:100]}")

        # 钉钉通知
        dingtalk_token = os.environ.get("DINGTALK_WEBHOOK_URL", "")
        if dingtalk_token:
            import requests as req
            ok_m = sum(1 for m in matches if (m.get("article_count", 0) or 0) >= 16)
            text = (
                f"## ⚽ 世界杯数据更新\n\n"
                f"**时间**: {time.strftime('%m-%d %H:%M')}\n\n"
                f"**文章总数**: {len(news)} 篇\n\n"
                f"**比赛覆盖**: {ok_m}/{len(matches)} 场达标\n\n"
                f"📥 [下载 Excel](https://github.com/{repo}/raw/main/data/worldcup_latest.xlsx)\n\n"
                f"💻 [打开网页](http://localhost:8001/matches)"
            )
            try:
                req.post(dingtalk_token, json={"msgtype": "markdown", "markdown": {"title": "世界杯数据更新", "text": text}}, timeout=10)
                log.info("✓ 钉钉通知已发送")
            except Exception as e:
                log.warning(f"钉钉通知失败: {e}")

    except Exception as e:
        log.exception(f"导出失败: {e}")


def main():
    p = argparse.ArgumentParser(description="世界杯新闻爬虫")
    p.add_argument("--quick", action="store_true", help="快速：每源 5 篇")
    p.add_argument("--backfill", action="store_true", help="存量：每源 20 篇")
    p.add_argument("--incremental", action="store_true", help="增量：每源 30 篇（跳过已有）")
    p.add_argument("--full", action="store_true", help="全量：每源 50 篇")
    p.add_argument("--source", type=str, default="", help="指定源，逗号分隔：fifa,espn")
    p.add_argument("--serve", action="store_true", help="启动 Web + 定时任务")
    p.add_argument("--init-db", action="store_true", help="初始化数据库")
    p.add_argument("--retag", action="store_true", help="重建所有新闻标签")
    # 比赛中心模式
    p.add_argument("--harvest-4d", action="store_true", help="为未来 4 天比赛每场挖 16 篇")
    p.add_argument("--incremental-3d", action="store_true", help="为未来 1-3 天比赛增量挖")
    p.add_argument("--harvest-match", type=int, metavar="ID", help="为指定比赛 ID 挖")
    p.add_argument("--refresh-schedule", action="store_true", help="重爬 Wikipedia 赛程")
    p.add_argument("--retag-matches", action="store_true", help="重建所有比赛的新闻关联")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    setup_logging(args.verbose)

    if args.init_db:
        from db.init_db import init_db
        init_db()
        return

    if args.retag:
        import tagger
        tagger.rebuild_all_tags()
        return

    if args.serve:
        serve_mode()
        return

    # 比赛中心模式
    if args.harvest_4d:
        from scrapers.match_harvester import harvest_upcoming
        harvest_upcoming(days=4, target=16)
        return
    if args.incremental_3d:
        from scrapers.match_harvester import harvest_upcoming
        harvest_upcoming(days=3, target=16)
        return
    if args.harvest_match:
        from scrapers.match_harvester import harvest_for_match
        m = store.get_match(args.harvest_match)
        if m:
            harvest_for_match(m, target=16)
        else:
            log.error(f"match {args.harvest_match} not found")
        return
    if args.refresh_schedule:
        from scrapers.schedule_wikipedia import main as sched_main
        sched_main()
        return
    if args.retag_matches:
        from match_tagger import tag_all_upcoming
        tag_all_upcoming(days=4)
        return

    # 确认 DB 已初始化
    if not os.path.exists(os.path.join(BASE_DIR, "db", "worldcup.db")):
        log.info("DB 不存在，先初始化…")
        from db.init_db import init_db
        init_db()

    sources = [s.strip() for s in args.source.split(",") if s.strip()] if args.source else []
    if args.quick:
        run_crawl(sources, max_per_source=5, skip_existing=True)
    elif args.backfill:
        run_crawl(sources, max_per_source=20, skip_existing=True)
    elif args.incremental:
        run_crawl(sources, max_per_source=30, skip_existing=True)
    elif args.full:
        run_crawl(sources, max_per_source=50, skip_existing=True)
    else:
        # 默认：跑 quick
        log.info("未指定模式，默认 --quick（5 篇/源）")
        run_crawl(sources, max_per_source=5, skip_existing=True)


if __name__ == "__main__":
    main()
