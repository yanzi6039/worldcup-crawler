"""
交互式 Playwright 工具：你输 URL，弹出浏览器，等你按键后 dump HTML 给你看

用法：
  python3 scrapers/inspect_url.py URL
  python3 scrapers/inspect_url.py  # 进入交互模式
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright


def inspect(url: str, save_path: str = None):
    print(f"🌐 打开 {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            locale="en-US",
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        print("\n⏸️  浏览器已打开。请：")
        print("   1. 如果有 captcha，点掉它")
        print("   2. 滚动页面让内容加载")
        print("   3. 回到此终端按 Enter 继续")
        input("   👉 按 Enter 开始 dump HTML... ")

        html = page.content()
        print(f"\n✓ 拿到 HTML: {len(html)} 字节")

        # 简要统计（用 html.parser 避免 lxml 依赖）
        from bs4 import BeautifulSoup
        from collections import Counter
        from urllib.parse import urljoin, urlparse
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.string if soup.title else "NONE"
        a_tags = soup.select("a[href]")
        print(f"  title: {title}")
        print(f"  total <a>: {len(a_tags)}")

        # 把所有 href 的路径模式（前 2 段）统计出来
        base_host = urlparse(url).netloc
        path_patterns = Counter()
        internal_hrefs = []
        for a in a_tags:
            href = a.get("href", "")
            if not href or href.startswith("#") or href.startswith("javascript"):
                continue
            full = urljoin(url, href)
            # 只看同域
            if urlparse(full).netloc and urlparse(full).netloc != base_host and "fifa.com" not in full:
                continue
            path = urlparse(full).path
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2:
                pattern = "/".join(parts[:2])
                path_patterns[pattern] += 1
            internal_hrefs.append((full, a.get_text(" ", strip=True)[:60]))

        print(f"\n  📊 内部 href 路径模式 top 10:")
        for pat, cnt in path_patterns.most_common(10):
            print(f"    {cnt:3d}  /{pat}/...")

        # 找看起来像文章的（路径里有日期、长 slug、特定模式）
        print(f"\n  📰 可能是文章的 href（前 15）:")
        for href, txt in internal_hrefs[:30]:
            # 只显示有较长路径段的
            path = urlparse(href).path
            parts = [p for p in path.split("/") if p]
            if any(len(p) > 15 for p in parts):  # 长 slug
                print(f"    {href[:90]}")
                print(f"      text: {txt}")

        if save_path:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"\n💾 HTML 已保存到 {save_path}")

        print("\n浏览器保持打开，再按 Enter 关闭")
        input("   👉 按 Enter 退出... ")
        browser.close()


def main():
    if len(sys.argv) > 1:
        url = sys.argv[1]
        save = sys.argv[2] if len(sys.argv) > 2 else None
        inspect(url, save)
    else:
        # 交互模式
        while True:
            url = input("\n🌐 输入 URL（或 q 退出）: ").strip()
            if url.lower() in ("q", "quit", "exit"):
                break
            if not url:
                continue
            if not url.startswith("http"):
                url = "https://" + url
            try:
                inspect(url)
            except Exception as e:
                print(f"✗ 错误: {e}")


if __name__ == "__main__":
    main()
