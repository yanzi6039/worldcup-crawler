"""
爬虫基类
- 请求重试 + 指数退避
- UA / Accept-Language 轮换
- 随机延迟（防压垮对方）
- 三档策略：requests / playwright / curl_cffi
- SQLite 写入（调 db.store）
"""
import os
import sys
import time
import random
import re
import logging
from typing import Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import requests
from bs4 import BeautifulSoup

from config import (UA_POOL, ACCEPT_LANGUAGE_POOL,
                    REQUEST_DELAY_MIN, REQUEST_DELAY_MAX,
                    RETRY_MAX, RETRY_BACKOFF_BASE, PAGE_TIMEOUT,
                    NEWS_MAX_AGE_DAYS)
from db import store

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(BASE_DIR, "logs", "crawl.log"),
                            encoding="utf-8"),
    ],
)
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
log = logging.getLogger("scraper")


def _random_headers(extra: dict = None) -> dict:
    h = {
        "User-Agent": random.choice(UA_POOL),
        "Accept-Language": random.choice(ACCEPT_LANGUAGE_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        # 注意：不放 br，requests 不自动解 brotli；gzip/deflate 它自动解
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if extra:
        h.update(extra)
    return h


def _backoff(attempt: int):
    wait = (attempt + 1) * RETRY_BACKOFF_BASE
    log.warning(f"  retry backoff {wait}s (attempt {attempt+1}/{RETRY_MAX})")
    time.sleep(wait)


def _throttle():
    """请求间随机延迟"""
    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))


# ============ 三档抓取 ============

def fetch_requests(url: str, extra_headers: dict = None) -> Optional[str]:
    """Tier 1：requests 直接抓（Python 3.14 TLS 兼容：verify=False）"""
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    for attempt in range(RETRY_MAX):
        try:
            r = requests.get(url, headers=_random_headers(extra_headers),
                             timeout=PAGE_TIMEOUT, verify=False)
            if r.status_code == 200:
                _throttle()
                return r.text
            if r.status_code in (429, 403, 503):
                log.warning(f"  {url} -> {r.status_code}, anti-bot triggered")
                _backoff(attempt)
                continue
            log.warning(f"  {url} -> {r.status_code}")
            return None
        except (requests.RequestException, OSError, ConnectionError) as e:
            log.warning(f"  {url} -> {type(e).__name__}: {e}")
            _backoff(attempt)
    return None


def fetch_curl_cffi(url: str, extra_headers: dict = None) -> Optional[str]:
    """Tier 3：curl_cffi 模拟 Chrome TLS 指纹"""
    try:
        from curl_cffi import requests as cf
    except ImportError:
        log.error("curl_cffi not installed")
        return None
    for attempt in range(RETRY_MAX):
        try:
            r = cf.get(url, headers=_random_headers(extra_headers),
                       impersonate="chrome120", timeout=PAGE_TIMEOUT)
            if r.status_code == 200:
                _throttle()
                return r.text
            _backoff(attempt)
        except Exception as e:
            log.warning(f"  curl_cffi {url} -> {e}")
            _backoff(attempt)
    return None


# Playwright 浏览器池（懒加载）
_pw_browser = None
_pw_playwright = None


def _get_browser():
    global _pw_browser, _pw_playwright
    if _pw_browser is None:
        from playwright.sync_api import sync_playwright
        _pw_playwright = sync_playwright().start()
        _pw_browser = _pw_playwright.chromium.launch(headless=True)
        log.info("✓ playwright browser launched")
    return _pw_browser


def fetch_playwright(url: str, extra_headers: dict = None,
                     wait_selector: str = None,
                     scroll: bool = True) -> Optional[str]:
    """Tier 2：Playwright + 滚动 + 鼠标移动"""
    browser = _get_browser()
    context = None
    page = None
    try:
        context = browser.new_context(
            user_agent=random.choice(UA_POOL),
            locale="en-US",
            viewport={"width": 1366 + random.randint(0, 200),
                      "height": 768 + random.randint(0, 200)},
        )
        if extra_headers:
            context.set_extra_http_headers(extra_headers)
        page = context.new_page()
        # 先 domcontentloaded 让页面起壳，再等 selector 或 networkidle
        try:
            page.goto(url, timeout=PAGE_TIMEOUT * 1000, wait_until="domcontentloaded")
        except Exception:
            # 跳转中，再等一下
            time.sleep(2)

        # 等 selector 出现（最关键）
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=15000)
            except Exception:
                # 退一步：等 networkidle
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
        else:
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

        # 模拟人类行为：滚动
        if scroll:
            for _ in range(random.randint(2, 4)):
                page.mouse.move(random.randint(100, 1000), random.randint(100, 600))
                page.evaluate(f"window.scrollBy(0, {random.randint(200, 600)})")
                time.sleep(random.uniform(0.5, 1.5))

        time.sleep(random.uniform(1.0, 2.5))
        html = page.content()
        _throttle()
        return html
    except Exception as e:
        log.warning(f"  playwright {url} -> {type(e).__name__}: {e}")
        return None
    finally:
        if page:
            try: page.close()
            except Exception: pass
        if context:
            try: context.close()
            except Exception: pass


def close_browser():
    global _pw_browser, _pw_playwright
    if _pw_browser:
        _pw_browser.close()
        _pw_browser = None
    if _pw_playwright:
        _pw_playwright.stop()
        _pw_playwright = None


# ============ 解析工具 ============

def parse_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# 正文里要剔除的"垃圾"短语（导航/分享/订阅/相关推荐等）
_BOILERPLATE_PATTERNS = [
    # 社交分享
    r"^Copy link$", r"^Facebook$", r"^X$", r"^Twitter$", r"^Pinterest$", r"^Email$",
    r"^WhatsApp$", r"^LinkedIn$", r"^Reddit$", r"^Telegram$",
    r"^Share this article$", r"^Share$", r"^Shares?$",
    r"^Join the conversation$", r"^Follow us$", r"^Add us .* Google$",
    # 订阅
    r"^Subscribe.*$", r"^Newsletter$", r"^Sign up.*$", r"^Sign in.*$",
    r"^Already a subscriber.*$", r"^Subscribe from just .*$",
    # 相关/推荐
    r"^More .* stories$", r"^Related.*$", r"^You might also like.*$",
    r"^See more$", r"^View gallery$", r"^Read more$",
    # 通用导航
    r"^Jump To:$", r"^Back to top$", r"^Skip to.*$", r"^Menu$", r"^Search$",
    r"^Home$", r"^News$", r"^Videos$", r"^Photos?$",
    # 广告位
    r"^Advertisement.*$", r"^Sponsored.*$", r"^Promoted.*$",
    # 图片说明（保留有用的，去掉纯 credit）
    r"^\(Image credit:.*\)$", r"^Photo by.*$", r"^Getty Images.*$", r"^Action Images.*$",
    # 评论提示
    r"^Comments?$", r"^Be the first to comment.*$",
    # FFT 特有
    r"^The 2026 World Cup is here.*$", r"^Watch every.*$", r"^Stream every.*$",
]
_BOILERPLATE_RE = re.compile("|".join(_BOILERPLATE_PATTERNS), re.IGNORECASE)


def extract_text(soup: BeautifulSoup, selector: str = "article, main, .article-body, .content") -> str:
    """
    提取正文。策略：
    1. 删 script/style/nav/footer/header/aside/form/noscript/svg/button 等明显非正文
    2. 限定到 selector 容器（article/main）
    3. **优先提取 <p>** 标签（新闻正文都在 p 里，不受 class 干扰）
    4. 若 p 累计 > 200 字就用 p；否则降级到容器全文
    5. 按行清洗：去 boilerplate（分享/订阅/导航短语）
    """
    # 1. 删非正文标签
    for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                     "form", "noscript", "svg", "button", "iframe"]):
        tag.decompose()

    # 2. 限定容器
    main = soup.select_one(selector) or soup.body or soup

    # 3. 提取 p 标签
    paragraphs = []
    for p in main.find_all("p"):
        txt = p.get_text(" ", strip=True)
        if not txt:
            continue
        # 跳过过短的（<20 字，可能是 caption/button）
        if len(txt) < 20:
            continue
        # 跳过明显是 boilerplate 的
        if _BOILERPLATE_RE.match(txt):
            continue
        paragraphs.append(txt)

    # 4. 若 p 累计足够长，用 p；否则降级到全文（按行清洗）
    if len("".join(paragraphs)) >= 200:
        return "\n\n".join(paragraphs)

    # 5. 降级：容器全文按行清洗
    text = main.get_text("\n", strip=True)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _BOILERPLATE_RE.match(line):
            continue
        if len(line) < 3 and not re.search(r"[\u4e00-\u9fa5a-zA-Z]", line):
            continue
        lines.append(line)
    return "\n".join(lines)


# ============ 基类 ============

class BaseScraper:
    """所有源爬虫的基类"""
    name = ""           # 源名（fifa/dongqiudi 等）
    tier = 1
    list_url = ""
    article_selector = ""

    def __init__(self):
        self.source = store.get_source_by_name(self.name)
        if not self.source:
            raise RuntimeError(f"source '{self.name}' not found in DB, run init_db first")
        self.source_id = self.source["id"]

    def fetch_list(self) -> str:
        """抓列表页 HTML"""
        if self.tier == 1:
            return fetch_requests(self.list_url)
        elif self.tier == 2:
            return fetch_playwright(self.list_url)
        else:
            return fetch_curl_cffi(self.list_url)

    def fetch_article(self, url: str) -> str:
        if self.tier == 1:
            return fetch_requests(url)
        elif self.tier == 2:
            return fetch_playwright(url)
        else:
            return fetch_curl_cffi(url)

    def parse_list(self, html: str) -> list[dict]:
        """
        解析列表页，返回 [{url, title, ...}, ...]
        子类必须重写
        """
        raise NotImplementedError

    def parse_article(self, html: str, url: str) -> dict:
        """
        解析文章页，返回 {title, summary, content, author, published_at, image_url, ...}
        子类必须重写
        """
        raise NotImplementedError

    def crawl(self, max_articles: int = 1000, skip_existing: bool = True) -> tuple[int, int]:
        """
        主流程：抓列表 → 抓每篇文章 → 入库。返回 (found, new)
        默认 max_articles=1000（不限），用 3 天 cutoff 自动停。

        ⭐ 3 天 cutoff hook：列表里遇到 published_at 超过 NEWS_MAX_AGE_DAYS 天的，
        RSS 按时间倒序的情况下，直接 break 整个源（不再爬后续老文章）。
        """
        started = time.time()
        log.info(f"▶ {self.name} starting (tier={self.tier}, max={max_articles}, cutoff={NEWS_MAX_AGE_DAYS}d)")
        try:
            list_html = self.fetch_list()
            if not list_html:
                log.error(f"✗ {self.name} list fetch failed")
                store.log_crawl(self.source_id, self.name, 0, 0,
                                status="failed", error="list fetch failed",
                                started_at=started)
                return (0, 0)

            items = self.parse_list(list_html)
            log.info(f"  {self.name} found {len(items)} links")

            found = 0
            new = 0
            cutoff_hit = False
            for item in items[:max_articles]:
                url = item.get("url")
                if not url:
                    continue

                # ⭐ 3 天 cutoff：列表项带了 published_at 就检查
                if item.get("published_at"):
                    if _is_too_old(item["published_at"]):
                        log.info(f"  ⏰ cutoff hit: '{item.get('title','')[:40]}' ({item['published_at']}) 超过 {NEWS_MAX_AGE_DAYS} 天，停止本源")
                        cutoff_hit = True
                        break

                if skip_existing and store.news_exists(url):
                    continue
                found += 1

                article_html = self.fetch_article(url)
                if not article_html:
                    continue

                try:
                    article = self.parse_article(article_html, url)
                except Exception as e:
                    log.warning(f"  parse failed {url}: {e}")
                    continue

                if not article.get("title"):
                    continue

                article.update({
                    "url": url,
                    "source_id": self.source_id,
                    "source_name": self.name,
                    "language": self.source["language"],
                    # published_at 优先用文章页解析的，没有就用列表项的（RSS 通常带时间）
                    "published_at": article.get("published_at") or item.get("published_at"),
                })
                news_id = store.insert_news(article)
                if news_id:
                    new += 1
                    log.info(f"  ✓ #{news_id} {article['title'][:50]}")

                    # 触发打标（懒加载，避免循环依赖）
                    try:
                        from tagger import tag_article
                        tag_article(news_id, article["title"],
                                    article.get("content", "") + " " + (item.get("title") or ""))
                    except Exception as e:
                        log.warning(f"  tag failed: {e}")

            store.update_source_crawl(self.source_id, new)
            msg = f"found={found} new={new}"
            if cutoff_hit:
                msg += f" (cutoff@{NEWS_MAX_AGE_DAYS}d)"
            store.log_crawl(self.source_id, self.name, found, new,
                            started_at=started)
            log.info(f"✓ {self.name} done: {msg} in {time.time()-started:.1f}s")
            return (found, new)
        except Exception as e:
            log.exception(f"✗ {self.name} error: {e}")
            store.log_crawl(self.source_id, self.name, 0, 0,
                            status="failed", error=str(e), started_at=started)
            return (0, 0)


def _is_too_old(date_str: str) -> bool:
    """判断日期是否超过 NEWS_MAX_AGE_DAYS 天"""
    if not date_str:
        return False
    # 尝试多种格式解析
    from datetime import datetime, timedelta
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%SZ", "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S %Z", "%d %b %Y %H:%M:%S %z"):
        try:
            dt = datetime.strptime(date_str[:26], fmt)
            # 统一到 naive UTC 比较（近似）
            if dt.tzinfo:
                from datetime import timezone
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            age = datetime.utcnow() - dt
            return age.days >= NEWS_MAX_AGE_DAYS
        except Exception:
            continue
    # 解析不出来就不挡（保守）
    return False
