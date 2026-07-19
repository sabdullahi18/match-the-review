"""Letterboxd review scraper.

Scrapes public review pages (https://letterboxd.com/{user}/reviews/films/page/N/).

- only fetches the first MAX_PAGES pages per user (~12 reviews per page)
- random delay between pagination requests
- 24h on-disk cache so repeat games don't re-hit Letterboxd

"""

from __future__ import annotations
from curl_cffi import requests
from bs4 import BeautifulSoup
from game import Review
import re

try:
    import httpx
except ImportError:
    httpx = None

url = "https://letterboxd.com/{user}/reviews/films/page/1/"
headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

spoiler_marker = "this review may contain spoilers"

print(f"Fetching {url}...")
response = requests.get(url, headers=headers, impersonate="chrome120")

if response.status_code == 200:
    with open("sample_page.html", "w", encoding="utf-8") as f:
        f.write(response.text)
    print("Success! Saved to 'sample_page.html'.")
else:
    print(f"Failed. Status code: {response.status_code}")

#########
# Parsing
#########


def parse_reviews_page(html: str, username: str) -> list[Review]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[Review] = []

    entries = soup.select("article.production-viewing")

    for entry in entries:
        content = entry.select_one("div.body") or entry
        title_link = content.select_one("h2.primaryname a")
        if not title_link:
            continue
        film = title_link.get_text(strip=True)

        year = None
        year_el = content.select_one(".releasedate")
        if year_el:
            m = re.search(r"(19|20)\d{2}", year_el.get_text())
            if m:
                year = m.group(0)

        rating = None
        rating_svg = content.select_one("span.inline-rating svg")
        if rating_svg and rating_svg.has_attr("aria-label"):
            star_str = rating_svg["aria-label"]
            rating = star_str.count("★") + (0.5 if "½" in star_str else 0.0)

        body = content.select_one(".js-review-body")
        if not body:
            continue

        paragraphs = [
            p.get_text(" ", strip=True)
            for p in body.find_all("p")
            if spoiler_marker not in p.get_text(strip=True).lower()
        ]
        text = "\n\n".join(p for p in paragraphs if p).strip()

        if not text:
            continue

        out.append(
            Review(username=username, film=film, year=year, rating=rating, text=text)
        )

    return out
