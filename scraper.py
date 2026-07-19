"""Letterboxd review scraper.

Scrapes public review pages (https://letterboxd.com/{user}/reviews/films/page/N/).

- only fetches the first MAX_PAGES pages per user (~12 reviews per page)
- random delay between pagination requests
- 24h on-disk cache so repeat games don't re-hit Letterboxd

"""

from __future__ import annotations
from curl_cffi import requests

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

print(f"Fetching {url}...")
response = requests.get(url, headers=headers, impersonate="chrome120")

if response.status_code == 200:
    with open("sample_page.html", "w", encoding="utf-8") as f:
        f.write(response.text)
    print("Success! Saved to 'sample_page.html'.")
else:
    print(f"Failed. Status code: {response.status_code}")
