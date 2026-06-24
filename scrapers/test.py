import requests
from bs4 import BeautifulSoup


def parse_article_payload(payload):
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("message", "api request failed"))

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

    return {
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


def fetch_article_payload(api_url, headers):
    response = requests.get(api_url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    raise SystemExit("Manual probe only: call fetch_article_payload(api_url, headers) explicitly.")
