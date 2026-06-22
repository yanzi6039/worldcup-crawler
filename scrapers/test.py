import requests
from bs4 import BeautifulSoup

response = requests.get(api_url, headers=headers, timeout=30)
response.raise_for_status()

payload = response.json()

if payload.get("code") != 0:
    raise RuntimeError(payload.get("message", "接口请求失败"))

article = payload["data"]
body_soup = BeautifulSoup(article.get("body", ""), "html.parser")

text = body_soup.get_text("\n", strip=True)

images = [
    img.get("data-src") or img.get("src") or img.get("orig-src")
    for img in body_soup.select("img")
]

tags = [
    {
        "id": channel.get("id"),
        "name": channel.get("tag"),
        "href": channel.get("href"),
        "thumb": channel.get("thumb"),
    }
    for channel in article.get("infos", {}).get("channels", [])
]

result = {
    "article_id": article.get("article_id"),
    "title": article.get("title"),
    "published_at": article.get("time"),
    "writer": article.get("writer"),
    "source": article.get("source"),
    "text": text,
    "images": images,
    "thumb": article.get("thumb"),
    "visit_total": article.get("visit_total"),
    "tags": tags,
}

print(result)