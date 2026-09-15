from datetime import datetime
import hashlib
import re
from bs4 import BeautifulSoup
import feedparser
import httpx
from .database import get_db
from .feeds import FEEDS


def clean_html(raw_html: str) -> str:
  if not raw_html:
    return ''
  soup = BeautifulSoup(raw_html, 'html.parser')
  text = soup.get_text(separator=' ', strip=True)
  return re.sub(r'\s+', ' ', text)


def compute_hash(url: str, title: str) -> str:
  canonical = f'{url.strip().lower()}:{title.strip().lower()}'
  return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


async def fetch_feed(client: httpx.AsyncClient, feed_meta: dict):
  try:
    response = await client.get(
        feed_meta['url'],
        headers={'User-Agent': 'AllInOneNewsBot/1.0 (+http://localhost)'},
        timeout=10.0,
    )
    parsed = feedparser.parse(response.content)

    items = []
    for entry in parsed.entries[:25]:
      title = getattr(entry, 'title', '').strip()
      link = getattr(entry, 'link', '').strip()
      if not title or not link:
        continue

      summary = getattr(entry, 'summary', '') or getattr(
          entry, 'description', ''
      )
      summary_clean = clean_html(summary)[:350]

      # Parse publish date or fallback to now
      pub_parsed = getattr(entry, 'published_parsed', None)
      if pub_parsed:
        pub_dt = datetime(*pub_parsed[:6])
      else:
        pub_dt = datetime.utcnow()

      guid = compute_hash(link, title)
      # Classify deep-dive vs wire based on summary length and source
      content_type = (
          'analysis'
          if (
              len(summary_clean) > 220
              or feed_meta['domain'] in ['economist.com', 'ft.com']
          )
          else 'wire'
      )

      items.append((
          guid,
          title,
          link,
          summary_clean,
          feed_meta['name'],
          feed_meta['domain'],
          feed_meta['region'],
          feed_meta['category'],
          content_type,
          pub_dt.isoformat(),
      ))

    with get_db() as conn:
      cursor = conn.cursor()
      cursor.executemany(
          """
                INSERT OR IGNORE INTO articles (
                    guid_hash, title, url, summary, source_name,
                    source_domain, region, category, content_type, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
          items,
      )
      conn.commit()

  except Exception as err:
    print(f"[Worker] Error fetching {feed_meta['name']}: {err}")


async def sync_all_feeds():
  async with httpx.AsyncClient() as client:
    for feed in FEEDS:
      await fetch_feed(client, feed)