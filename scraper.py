"""Letterboxd review scraper.

Scrapes public review pages (https://letterboxd.com/{username}/reviews/films/page/N/).

- only fetches the first MAX_PAGES pages per user (~12 reviews per page)
- random delay between pagination requests
- 24h on-disk cache so repeat games don't re-hit Letterboxd

"""

from __future__ import annotations
from bs4 import BeautifulSoup
from game import Review
from pathlib import Path
from dataclasses import asdict
import re
import json
import time
import asyncio
import random

try:
    from curl_cffi.requests import AsyncSession
except ImportError:
    AsyncSession = None

URL = "https://letterboxd.com/{username}/reviews/films/page/{page}/"
HEADERS = {
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
SPOILER_MARKER = "this review may contain spoilers"
MAX_PAGES = 50
CACHE_DIR = Path(".cache")
CACHE_TTL_SECONDS = 24 * 3600
REQUEST_TIMEOUT = 15.0

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
            if SPOILER_MARKER not in p.get_text(strip=True).lower()
        ]
        text = "\n\n".join(p for p in paragraphs if p).strip()

        if not text:
            continue

        out.append(
            Review(username=username, film=film, year=year, rating=rating, text=text)
        )

    return out


#########
# Caching
#########


def _cache_path(username: str) -> Path:
    safe = re.sub(r"[^a-z0-9_-]", "_", username.lower())
    return CACHE_DIR / f"{safe}.json"


def _cache_load(username: str) -> list[Review] | None:
    path = _cache_path(username)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data["fetched_at"] > CACHE_TTL_SECONDS:
            return None
        return [Review(**r) for r in data["reviews"]]
    except Exception:
        return None


def _cache_save(username: str, reviews: list[Review]) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    _cache_path(username).write_text(
        json.dumps(
            {
                "fetched_at": time.time(),
                "reviews": [asdict(r) for r in reviews],
            }
        )
    )


##########
# Fetching
##########


class ScrapeError(Exception):
    pass


async def fetch_user_reviews(
    client: AsyncSession, username: str, max_pages: int = MAX_PAGES
) -> list[Review]:
    cached = _cache_load(username)
    if cached is not None:
        return cached

    reviews: list[Review] = []
    for page in range(1, max_pages + 1):
        url = URL.format(username=username, page=page)

        resp = await client.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)

        if resp.status_code == 404:
            if page == 1:
                raise ScrapeError(f"Letterboxd user '{username}' not found")
            break
        if resp.status_code != 200:
            break  # rate limited / Cloudflare block / hiccup: keep whatever we have so far

        page_reviews = parse_reviews_page(resp.text, username)
        if not page_reviews:
            break  # ran out of reviews

        reviews.extend(page_reviews)
        await asyncio.sleep(random.uniform(0.4, 0.9))

    _cache_save(username, reviews)
    return reviews


if __name__ == "__main__":

    async def run_single_user_test():
        test_user = "user"
        print(f"Initialising test async session for user: {test_user}")

        async with AsyncSession(impersonate="chrome120") as client:
            try:
                reviews = await fetch_user_reviews(
                    client, username=test_user, max_pages=2
                )

                print(f"\nTotal reviews captured: {len(reviews)}")
                for idx, r in enumerate(reviews[:3], 1):
                    stars = f"{r.rating}★" if r.rating else "unrated"
                    print(f"\n[{idx}] {r.film} ({r.year}) - {stars}")
                    print(f"    Text snippet: {r.text[:80]}...")

            except ScrapeError as e:
                print(f"ScrapeError caught: {e}")
            except Exception as e:
                print(f"Unexpected error during fetch: {e}")

    asyncio.run(run_single_user_test())
