"""
Tier 1 友好源
策略：能 RSS 就 RSS（XML 解析、无反爬、含标题/链接/时间），不能再 HTML 爬

- FourFourTwo: RSS（已验证，50 篇/次）
- ESPN: RSS（/soccer/rss）
- Rotowire: HTML 爬
- Goal: RSS（多 URL 试探）
- FIFA: HTML / Playwright（v1）
"""
import os
import sys
import time
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup

from base_scraper import BaseScraper, parse_html, extract_text, fetch_requests, log


# ============ RSS 工具 ============

def parse_rss(xml_text: str, base_url: str = "", max_items: int = 30) -> list[dict]:
    """解析 RSS 2.0 / Atom feed，统一返回 [{url, title, summary, published_at, author}, ...]"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning(f"  rss parse error: {e}")
        return []

    items = []
    # RSS 2.0: channel/item
    for it in root.findall(".//item"):
        link = (it.findtext("link") or "").strip()
        # 有些 RSS 把 link 放 <atom:link href="..."/>
        if not link:
            atom_link = it.find("{http://www.w3.org/2005/Atom}link")
            if atom_link is not None:
                link = atom_link.get("href", "")
        if not link:
            continue
        if base_url and not link.startswith("http"):
            link = urljoin(base_url, link)
        title = (it.findtext("title") or "").strip()
        desc = (it.findtext("description") or "").strip()
        # 描述里可能是 HTML
        if desc and "<" in desc:
            desc = BeautifulSoup(desc, "lxml").get_text(" ", strip=True)
        pub = (it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date") or "").strip()
        author = (it.findtext("{http://purl.org/dc/elements/1.1/}creator") or it.findtext("author") or "").strip()
        items.append({
            "url": link,
            "title": title,
            "summary": desc[:300],
            "published_at": _normalize_date(pub),
            "author": author,
        })
        if len(items) >= max_items:
            break

    # Atom: feed/entry
    if not items:
        for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            link = ""
            for l in entry.findall("{http://www.w3.org/2005/Atom}link"):
                if l.get("rel", "alternate") == "alternate" or not link:
                    link = l.get("href", "")
            if not link:
                continue
            title = entry.findtext("{http://www.w3.org/2005/Atom}title") or ""
            summary = entry.findtext("{http://www.w3.org/2005/Atom}summary") or \
                      entry.findtext("{http://www.w3.org/2005/Atom}content") or ""
            if summary and "<" in summary:
                summary = BeautifulSoup(summary, "lxml").get_text(" ", strip=True)
            pub = entry.findtext("{http://www.w3.org/2005/Atom}published") or \
                   entry.findtext("{http://www.w3.org/2005/Atom}updated") or ""
            items.append({
                "url": link,
                "title": title.strip(),
                "summary": summary[:300],
                "published_at": _normalize_date(pub),
                "author": "",
            })
            if len(items) >= max_items:
                break
    return items


def _normalize_date(s: str) -> str:
    """各种日期格式归一化为 YYYY-MM-DD HH:MM:SS"""
    if not s:
        return ""
    # RFC822: "Sun, 21 Jun 2026 14:00:00 +0000"
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S %z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:26], fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return s[:19]  # 兜底截断


# ============ FourFourTwo（RSS） ============

class FourFourTwoScraper(BaseScraper):
    name = "fourfourtwo"
    tier = 1
    list_url = "https://www.fourfourtwo.com/rss"

    def parse_list(self, html: str) -> list[dict]:
        # FFT RSS 不限于世界杯，过滤标题含 world cup / 国家名 的
        all_items = parse_rss(html, "https://www.fourfourtwo.com")
        wc_items = [it for it in all_items if _is_world_cup_related(it["title"] + " " + it.get("summary", ""))]
        log.info(f"  fft: {len(all_items)} total, {len(wc_items)} world-cup related")
        return wc_items

    def parse_article(self, html: str, url: str) -> dict:
        soup = parse_html(html)
        title = (soup.select_one("h1") or {}).get_text(strip=True) if soup.select_one("h1") else ""
        content = extract_text(soup, "article, .article-body, [class*='articleContent']")
        summary = content[:200].replace("\n", " ").strip()
        img = soup.select_one("article img, picture img")
        image_url = (img.get("src") if img else "")
        return {"title": title, "summary": summary, "content": content, "image_url": image_url}


# ============ ESPN soccer RSS ============

class EspnScraper(BaseScraper):
    name = "espn"
    tier = 1
    list_url = "https://www.espn.com/espn/rss/soccer/news"

    def parse_list(self, html: str) -> list[dict]:
        all_items = parse_rss(html, "https://www.espn.com")
        wc_items = [it for it in all_items if _is_world_cup_related(it["title"] + " " + it.get("summary", ""))]
        log.info(f"  espn: {len(all_items)} total, {len(wc_items)} world-cup related")
        return wc_items

    def parse_article(self, html: str, url: str) -> dict:
        soup = parse_html(html)
        h1 = soup.select_one("h1")
        title = h1.get_text(strip=True) if h1 else ""
        content = extract_text(soup, "article, .article-body, [class*='story-body']")
        summary = content[:200].replace("\n", " ").strip()
        return {"title": title, "summary": summary, "content": content, "image_url": ""}


# ============ Rotowire HTML ============

class RotowireScraper(BaseScraper):
    name = "rotowire"
    tier = 1
    list_url = "https://www.rotowire.com/soccer/news.php"

    def parse_list(self, html: str) -> list[dict]:
        soup = parse_html(html)
        items = []
        seen = set()
        for a in soup.select("a[href*='news/']"):
            href = a.get("href", "")
            if not href:
                continue
            url = urljoin("https://www.rotowire.com/", href.split("?")[0])
            if url in seen:
                continue
            seen.add(url)
            title = a.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            items.append({"url": url, "title": title})
        return [it for it in items if _is_world_cup_related(it["title"])]

    def parse_article(self, html: str, url: str) -> dict:
        soup = parse_html(html)
        h1 = soup.select_one("h1")
        title = h1.get_text(strip=True) if h1 else ""
        content = extract_text(soup, "article, .news-body, .article-body, main")
        summary = content[:200].replace("\n", " ").strip()
        return {"title": title, "summary": summary, "content": content, "image_url": ""}


# ============ 世界杯相关性过滤 ============

WC_KEYWORDS = [
    # 中英关键词
    "world cup", "World Cup", "world-cup",
    "国际足联", "世界杯",
    "2026",
    # 阶段
    "group stage", "knockout", "round of 16", "quarter-final", "semifinal", "final",
    # 主办
    "USA 2026", "Canada 2026", "Mexico 2026",
]

def _is_world_cup_related(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    if any(kw.lower() in text_lower for kw in WC_KEYWORDS):
        return True
    return False


# ============ 注册表 ============

class _GenericRssScraper(BaseScraper):
    """通用 RSS scraper：只配置 list_url 就能用"""
    name = "_generic"
    tier = 1
    list_url = ""

    def parse_list(self, html: str) -> list[dict]:
        all_items = parse_rss(html, self.list_url)
        wc = [it for it in all_items if _is_world_cup_related(it["title"] + " " + it.get("summary", ""))]
        log.info(f"  {self.name}: {len(all_items)} total, {len(wc)} world-cup")
        return wc

    def parse_article(self, html: str, url: str) -> dict:
        soup = parse_html(html)
        h1 = soup.select_one("h1")
        title = h1.get_text(strip=True) if h1 else ""
        content = extract_text(soup, "article, main, [class*='article'], [class*='story'], [class*='content']")
        summary = content[:200].replace("\n", " ").strip()
        img = soup.select_one("article img, picture img, meta[property='og:image']")
        image_url = ""
        if img:
            image_url = img.get("src") or img.get("content") or ""
        pub = ""
        t = soup.select_one("time")
        if t:
            pub = t.get("datetime") or t.get_text(strip=True)
        return {"title": title, "summary": summary, "content": content,
                "image_url": image_url, "published_at": pub}


def _make_rss_scraper(name: str, list_url: str):
    """动态生成一个 RSS scraper 类"""
    return type(
        f"RssScraper_{name}",
        (_GenericRssScraper,),
        {"name": name, "list_url": list_url},
    )


# 通用 RSS 源（运行时由 config 动态生成具体类）
_GENERIC_RSS_SOURCES = {
    "bbc_sport": "https://feeds.bbci.co.uk/sport/football/rss.xml",
    "guardian": "https://www.theguardian.com/football/rss",
    "skysports": "https://www.skysports.com/rss/12040",
    "90min": "https://www.90min.com/posts.rss",
}


TIER1_SCRAPERS = {
    "fourfourtwo": FourFourTwoScraper,
    "espn": EspnScraper,
    "rotowire": RotowireScraper,
}
# 动态注册通用 RSS 源
for _name, _url in _GENERIC_RSS_SOURCES.items():
    TIER1_SCRAPERS[_name] = _make_rss_scraper(_name, _url)

# 直播吧
from scrapers.zhibo8_scraper import Zhibo8Scraper
TIER1_SCRAPERS["zhibo8"] = Zhibo8Scraper
from scrapers.kickoff_scraper import KickoffScraper
TIER1_SCRAPERS["kickoff"] = KickoffScraper


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "fourfourtwo"
    cls = TIER1_SCRAPERS.get(target)
    if not cls:
        print(f"unknown: {target}; available: {list(TIER1_SCRAPERS.keys())}")
        sys.exit(1)
    s = cls()
    found, new = s.crawl(max_articles=10)
    print(f"\n=== {target}: found={found}, new={new} ===")
