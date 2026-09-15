import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path("data/news.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_db():
  conn = sqlite3.connect(DB_PATH)
  conn.row_factory = sqlite3.Row
  try:
    yield conn
  finally:
    conn.close()


def init_db():
  with get_db() as conn:
    cursor = conn.cursor()
    cursor.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guid_hash TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                summary TEXT,
                source_name TEXT NOT NULL,
                source_domain TEXT NOT NULL,
                region TEXT NOT NULL,
                category TEXT NOT NULL,
                content_type TEXT DEFAULT 'wire',
                published_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                cluster_key TEXT
            )
        """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_published_at ON"
        " articles(published_at DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_region ON articles(region)"
    )
    conn.commit()