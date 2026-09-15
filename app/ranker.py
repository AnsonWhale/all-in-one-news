from collections import defaultdict
from datetime import datetime, timezone
import math
import re

STOPWORDS = {
    'the',
    'and',
    'for',
    'that',
    'with',
    'from',
    'this',
    'will',
    'amid',
    'after',
    'says',
    'about',
    'into',
    'over',
    'more',
    'their',
    'than',
    'year',
    'market',
    'have',
    'been',
    'were',
    'also',
    'could',
    'under',
    'first',
    'australia',
    'australian',
}


def tokenize(text: str) -> set[str]:
  words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
  return {w for w in words if w not in STOPWORDS}


def calculate_jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
  if not set_a or not set_b:
    return 0.0
  return len(set_a & set_b) / len(set_a | set_b)


def rank_and_cluster_articles(raw_articles: list[dict], scope: str = 'global'):
  now = datetime.now(timezone.utc)
  processed = []

  for art in raw_articles:
    pub_time = datetime.fromisoformat(art['published_at']).replace(
        tzinfo=timezone.utc
    )
    age_hours = max(0.1, (now - pub_time).total_seconds() / 3600.0)

    # Base gravity
    gravity = 1.2 if art['content_type'] == 'analysis' else 1.8
    base_score = 10.0

    # Sentiment inference
    title_lower = art['title'].lower()
    if any(
        w in title_lower
        for w in ['rally', 'surge', 'boom', 'record', 'gain', 'growth']
    ):
      tone = 'bullish'
    elif any(
        w in title_lower
        for w in ['drop', 'risk', 'crisis', 'tariff', 'slump', 'warning']
    ):
      tone = 'caution'
    else:
      tone = 'neutral'

    processed.append({
        **art,
        'age_hours': age_hours,
        'base_score': base_score,
        'gravity': gravity,
        'tone': tone,
        'tokens': tokenize(art['title']),
        'perspectives': [],
    })

  # 1. Semantic Deduplication & Cross-Source Clustering
  clustered = []
  used_indices = set()

  for i, prime in enumerate(processed):
    if i in used_indices:
      continue

    cluster_matches = []
    for j in range(i + 1, len(processed)):
      if j in used_indices:
        continue
      cand = processed[j]
      if prime['source_domain'] == cand['source_domain']:
        continue

      similarity = calculate_jaccard_similarity(
          prime['tokens'], cand['tokens']
      )
      if similarity >= 0.35:
        cluster_matches.append(cand)
        used_indices.add(j)

    prime['perspectives'] = cluster_matches
    distinct_sources = len({m['source_domain'] for m in cluster_matches})
    prime['consensus_multiplier'] = 1.0 + (distinct_sources * 0.40)

    # Compute baseline decay rank
    raw_rank = (
        prime['base_score'] * prime['consensus_multiplier']
    ) / math.pow(prime['age_hours'] + 2, prime['gravity'])
    prime['raw_rank'] = raw_rank
    clustered.append(prime)

  # 2. Dynamic Fair-Share Quota Interleaving
  if scope == 'au':
    # Pure domestic focus
    ranked = [a for a in clustered if a['region'] == 'AU']
    ranked.sort(key=lambda x: x['raw_rank'], reverse=True)
  else:
    # GLOBAL BLENDED: Separate pools to guarantee representation
    global_pool = [a for a in clustered if a['region'] != 'AU']
    au_pool = [a for a in clustered if a['region'] == 'AU']

    global_pool.sort(key=lambda x: x['raw_rank'], reverse=True)
    au_pool.sort(key=lambda x: x['raw_rank'], reverse=True)

    ranked = []
    g_idx, a_idx = 0, 0
    domain_counts = defaultdict(int)

    # Max 1 AU story per 5 total stories (20% ceiling)
    while g_idx < len(global_pool) or a_idx < len(au_pool):
      au_count = sum(1 for r in ranked if r['region'] == 'AU')
      au_ratio = au_count / max(1, len(ranked))

      # Choose an AU story only if below 20% quota, otherwise prioritize global
      pick_au = False
      if a_idx < len(au_pool) and (au_ratio < 0.20 or g_idx >= len(global_pool)):
        pick_au = True

      if pick_au:
        candidate = au_pool[a_idx]
        a_idx += 1
      elif g_idx < len(global_pool):
        candidate = global_pool[g_idx]
        g_idx += 1
      else:
        candidate = au_pool[a_idx]
        a_idx += 1

      # Publisher saturation penalty
      d_count = domain_counts[candidate['source_domain']]
      penalty = 0.75**d_count
      domain_counts[candidate['source_domain']] += 1

      candidate['final_score'] = round(candidate['raw_rank'] * penalty * 10, 1)
      ranked.append(candidate)

  # Clean token sets for JSON serialization
  for item in ranked:
    item.pop('tokens', None)
    for p in item.get('perspectives', []):
      p.pop('tokens', None)

  return ranked