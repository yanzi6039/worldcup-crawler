"""
Kickoff Calendar 爬虫
Next.js SSR: /blog/tag/worldcup-2026, Playwright 渲染后提取
"""
import os, sys, time, random, logging
from urllib.parse import urljoin

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from base_scraper import BaseScraper, fetch_playwright, close_browser
from bs4 import BeautifulSoup

log = logging.getLogger("kickoff")


class KickoffScraper(BaseScraper):
    name = "kickoff"
    tier = 2
    list_url = "https://kickoff.guide/blog/tag/worldcup-2026"

    def crawl(self, max_articles: int = 50, skip_existing: bool = True):
        from db import store
        started = time.time()

        found, new = 0, 0
        try:
            html = fetch_playwright(self.list_url,
                                   wait_selector='.koc-blog-card', scroll=True)
            if not html:
                log.warning("kickoff: Playwright returned None")
                return (0, 0)

            soup = BeautifulSoup(html, 'lxml')
            cards = soup.select('.koc-blog-card')
            log.info(f"  kickoff: {len(cards)} cards found")

            for card in cards:
                link = card.select_one('a[href*="/blog/"]')
                h3 = card.select_one('h3')
                excerpt = card.select_one('.excerpt')
                time_el = card.select_one('time')
                badge = card.select_one('.koc-blog-badge')

                if not link or not h3:
                    continue
                href = link.get('href', '')
                url = urljoin('https://kickoff.guide/', href)
                title = h3.get_text(strip=True)
                summary = excerpt.get_text(strip=True) if excerpt else ''
                pub_time = time_el.get('dateTime', '') if time_el else ''
                category = badge.get_text(strip=True) if badge else ''

                if not title or len(title) < 5:
                    continue

                if skip_existing and store.news_exists(url):
                    continue
                found += 1

                nid = store.insert_news({
                    'url': url,
                    'title': title,
                    'summary': summary[:300],
                    'content': '',
                    'published_at': pub_time,
                    'source_name': self.name,
                    'language': 'en',
                    'image_url': '',
                })
                if nid:
                    new += 1
                    try:
                        from tagger import tag_article
                        tag_article(nid, title, summary)
                    except: pass
                if new >= max_articles:
                    break

            store.update_source_crawl(self.source_id, new)
            from db.store import log_crawl
            log_crawl(self.source_id, self.name, found, new, started_at=started)
            log.info(f"✓ kickoff: found={found} new={new}")
        except Exception as e:
            log.warning(f"kickoff error: {e}")

        return (found, new)

    def parse_list(self, html):
        return []
    def parse_article(self, html, url):
        return {}
