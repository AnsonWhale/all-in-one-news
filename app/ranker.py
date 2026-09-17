from collections import defaultdict
from datetime import datetime, timezone
import math
import re

STOPWORDS = {
    'the', 'and', 'for', 'that', 'with', 'from', 'this', 'will', 'amid',
    'after', 'says', 'about', 'into', 'over', 'more', 'their', 'than',
    'year', 'market', 'have', 'been', 'were', 'also', 'could', 'under',
    'first', 'australia', 'australian', 'report', 'today', 'week', 'what'
}


def tokenize(text: str) -> set[str]:
  words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
  return {w for w in words if w not in STOPWORDS}


def calculate_jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
  if not set_a or not set_b:
    return 0.0
  return len(set_a & set_b) / len(set_a | set_b)

def rank_and_cluster_articles(
    raw_articles: list[dict],
    scope: str = 'global',
    time_frame: str = 'daily'
):
  # ============================================================================
  # SECTION 1: DYNAMIC HORIZON CONFIGURATION
  # - In daily mode (24h), we want sharp gravity (1.6) so breaking, high-velocity
  #   news rapidly overtakes older items.
  # - In weekly mode (7d), steep gravity would bury 3-5 day old stories under trivial
  #   single-source wires published an hour ago. We drop gravity to 0.70 so older
  #   events stay competitive.
  # - Consensus boost is elevated in weekly mode (0.85 vs 0.45) so week-defining
  #   stories backed by 3+ outlets naturally rise to the top of the weekly digest.
  # ============================================================================
  now = datetime.now(timezone.utc)
  processed = []

  if time_frame == 'weekly':
    base_gravity = 0.70
    consensus_boost_factor = 0.85
  else:
    base_gravity = 1.60
    consensus_boost_factor = 0.45

  # ============================================================================
  # SECTION 2: NORMALIZATION, METADATA ENRICHMENT & SENTIMENT
  # - Calculate article age in hours, with a safe lower bound (0.2h) to avoid divide-by-zero.
  # - Apply timezone softening to global stories (0.65x multiplier) so domestic daylight
  #   publishing volume doesn't overpower overnight European and US market reports.
  # - Apply a lighter gravity curve to 'analysis' deep-dives compared to breaking 'wires'.
  # - Tokenize headlines into stopword-free word sets for semantic comparison.
  # ============================================================================
  for art in raw_articles:
    pub_time = datetime.fromisoformat(art['published_at']).replace(
        tzinfo=timezone.utc
    )
    age_hours = max(0.2, (now - pub_time).total_seconds() / 3600.0)

    # Timezone buffer: Soften decay curve for overnight global stories
    effective_age = age_hours if art['region'] == 'AU' else max(1.0, age_hours * 0.65)

    # Analytical articles retain shelf-life longer than standard wires
    gravity = base_gravity * 0.80 if art['content_type'] == 'analysis' else base_gravity
    base_score = 10.0

    # Sentiment classification
    title_lower = art['title'].lower()
    if any(w in title_lower for w in ['rally', 'surge', 'boom', 'record', 'gain', 'growth']):
      tone = 'bullish'
    elif any(w in title_lower for w in ['drop', 'risk', 'crisis', 'tariff', 'slump', 'warning', 'inflation']):
      tone = 'caution'
    else:
      tone = 'neutral'

    processed.append({
        **art,
        'age_hours': effective_age,
        'base_score': base_score,
        'gravity': gravity,
        'tone': tone,
        'tokens': tokenize(art['title']),
        'perspectives': [],
    })

  # ============================================================================
  # SECTION 3: SEMANTIC DEDUPLICATION & CROSS-SOURCE CLUSTERING
  # - Iterate over articles and compute Jaccard similarity across unique title tokens.
  # - If similarity >= 0.35 across distinct publisher domains, group the secondary
  #   story into the prime article's 'perspectives' list.
  # - Mark grouped secondary stories as used so they don't repeat as independent cards.
  # - Reward multi-source stories with a consensus multiplier. If in Global mode,
  #   dampen domestic Australian-only consensus (0.12) to prevent local media echo chambers.
  # ============================================================================
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

      similarity = calculate_jaccard_similarity(prime['tokens'], cand['tokens'])
      if similarity >= 0.35:
        cluster_matches.append(cand)
        used_indices.add(j)

    prime['perspectives'] = cluster_matches
    distinct_sources = len({m['source_domain'] for m in cluster_matches})

    # Consensus Boost: Dampen AU domestic agreements when evaluated in Global mode
    if scope == 'global' and prime['region'] == 'AU':
      prime['consensus_multiplier'] = 1.0 + (distinct_sources * 0.12)
    else:
      prime['consensus_multiplier'] = 1.0 + (distinct_sources * consensus_boost_factor)

    # Base decay rank calculated using the dynamic horizon gravity
    raw_rank = (prime['base_score'] * prime['consensus_multiplier']) / math.pow(
        prime['age_hours'] + 2, prime['gravity']
    )
    prime['raw_rank'] = raw_rank
    clustered.append(prime)

  # ============================================================================
  # SECTION 4: FAIR-SHARE QUOTA INTERLEAVING & PUBLISHER DIVERSITY
  # - In AU scope: Deliver exclusively Australian articles sorted by raw decay rank.
  # - In Global scope: Enforce a strict 20% ceiling on Australian stories so high domestic
  #   publishing volume cannot overwhelm international coverage.
  # - Apply an explicit score scale down (0.38x) to AU candidate scores in Global mode
  #   so display badges visually match their actual lower priority.
  # - Apply a publisher saturation penalty (0.75^count) to prevent any single domain
  #   from monopolizing consecutive positions.
  # ============================================================================
  if scope == 'au':
    ranked = [a for a in clustered if a['region'] == 'AU']
    ranked.sort(key=lambda x: x['raw_rank'], reverse=True)
    for a in ranked:
      a['final_score'] = round(a['raw_rank'] * 10, 1)
  else:
    # GLOBAL BLENDED: Separate pools to guarantee representation
    global_pool = [a for a in clustered if a['region'] != 'AU']
    au_pool = [a for a in clustered if a['region'] == 'AU']

    global_pool.sort(key=lambda x: x['raw_rank'], reverse=True)
    au_pool.sort(key=lambda x: x['raw_rank'], reverse=True)

    ranked = []
    g_idx, a_idx = 0, 0
    domain_counts = defaultdict(int)
    au_selected_count = 0

    while g_idx < len(global_pool) or a_idx < len(au_pool):
      au_ratio = au_selected_count / max(1, len(ranked))

      # Cap AU representation at maximum 20% in Global mode
      pick_au = False
      if a_idx < len(au_pool) and (au_ratio < 0.20 or g_idx >= len(global_pool)):
        pick_au = True

      if pick_au:
        candidate = au_pool[a_idx]
        a_idx += 1
        au_selected_count += 1
        regional_factor = 0.38  # Direct score dampener in Global mode
      elif g_idx < len(global_pool):
        candidate = global_pool[g_idx]
        g_idx += 1
        regional_factor = 1.0
      else:
        candidate = au_pool[a_idx]
        a_idx += 1
        regional_factor = 0.38

      # Publisher saturation penalty
      d_count = domain_counts[candidate['source_domain']]
      saturation_penalty = 0.75 ** d_count
      domain_counts[candidate['source_domain']] += 1

      # Calculate scaled score
      candidate['final_score'] = round(
          candidate['raw_rank'] * regional_factor * saturation_penalty * 10, 1
      )
      ranked.append(candidate)

  # ============================================================================
  # SECTION 5: JSON SERIALIZATION CLEANUP
  # - Python 'set' objects (used for fast token operations) cannot be serialized
  #   to JSON by FastAPI / Pydantic. We pop them out before returning.
  # ============================================================================
  for item in ranked:
    item.pop('tokens', None)
    for p in item.get('perspectives', []):
      p.pop('tokens', None)

  return ranked