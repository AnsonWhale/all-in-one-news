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
    'amid',
    'australia',
    'global',
    'says',
    'will',
    'over',
    'after',
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

    base_score = 10.0
    gravity = 1.2 if art['content_type'] == 'analysis' else 1.8

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

  # Semantic Clustering across distinct outlets
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
    # Consensus boost
    distinct_sources = len({m['source_domain'] for m in cluster_matches})
    prime['consensus_multiplier'] = 1.0 + (distinct_sources * 0.45)
    clustered.append(prime)

  # Final Score with Regional Balancing
  domain_counts = defaultdict(int)
  au_count_in_global = 0
  ranked_results = []

  # Sort roughly by raw freshness/score first
  clustered.sort(
      key=lambda x: (x['base_score'] * x['consensus_multiplier'])
      / math.pow(x['age_hours'] + 2, x['gravity']),
      reverse=True,
  )

  for item in clustered:
    raw_rank = (item['base_score'] * item['consensus_multiplier']) / math.pow(
        item['age_hours'] + 2, item['gravity']
    )

    # Regional Throttling
    if scope == 'global' and item['region'] == 'AU':
      raw_rank *= 0.35 * (0.60**au_count_in_global)
      au_count_in_global += 1

    # Domain Saturation Penalty
    d_count = domain_counts[item['source_domain']]
    if d_count >= 2:
      raw_rank *= 0.70 ** (d_count - 1)
    domain_counts[item['source_domain']] += 1

    item['final_score'] = round(raw_rank * 10, 1)
    item.pop('tokens', None)  # Clean out sets for JSON serialization
    for p in item['perspectives']:
      p.pop('tokens', None)

    ranked_results.append(item)

  ranked_results.sort(key=lambda x: x['final_score'], reverse=True)
  return ranked_results