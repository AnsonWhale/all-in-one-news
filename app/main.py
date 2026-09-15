from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .database import get_db, init_db, prune_old_articles
from .fetcher import sync_all_feeds
from .ranker import rank_and_cluster_articles

# Ping fast without checking database queries
@app.get("/health")
def health_check():
  return {"status": "ok"}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB tables & WAL mode
    init_db()

    scheduler = AsyncIOScheduler()

    # 1. Fetch & ingest RSS feeds every 20 minutes
    scheduler.add_job(sync_all_feeds, 'interval', minutes=20)

    # 2. Prune old records daily at 03:00 UTC (14-day retention)
    scheduler.add_job(
        prune_old_articles,
        trigger=CronTrigger(hour=3, minute=0),
        args=[14],
        id="db_cleanup_job",
        replace_existing=True,
    )

    scheduler.start()

    # Initial boot tasks: initial sync + prune on startup
    prune_old_articles(days=14)
    await sync_all_feeds()

    yield

    scheduler.shutdown()


app = FastAPI(title='All in One News Intelligence API', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.get('/api/news')
def get_ranked_news(
    scope: str = Query('global', regex='^(au|global)$'),
    time_frame: str = Query('daily', regex='^(daily|weekly)$'),
):
  hours = 24 if time_frame == 'daily' else 168
  cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

  with get_db() as conn:
    cursor = conn.cursor()
    if scope == 'au':
      cursor.execute(
          """
                SELECT * FROM articles 
                WHERE published_at >= ? AND region = 'AU'
                ORDER BY published_at DESC LIMIT 150
            """,
          (cutoff,),
      )
    else:
      cursor.execute(
          """
                SELECT * FROM articles 
                WHERE published_at >= ?
                ORDER BY published_at DESC LIMIT 200
            """,
          (cutoff,),
      )

    rows = [dict(r) for r in cursor.fetchall()]

  ranked = rank_and_cluster_articles(rows, scope=scope)
  return {'total': len(ranked), 'scope': scope, 'articles': ranked}


# Mount the frontend directory so browsing to http://localhost:8000 loads the app
app.mount('/', StaticFiles(directory='static', html=True), name='static')