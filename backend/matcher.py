import re
import logging
from collections import defaultdict
from typing import List, Dict, Any, Tuple
import numpy as np

_model = None

def get_model():
    global _model
    if _model is not None:
        return _model
    
    try:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading SentenceTransformer model 'all-MiniLM-L6-v2'...")
        _model = SentenceTransformer('all-MiniLM-L6-v2')
        return _model
    except Exception as e:
        logger.warning(f"Failed to load sentence-transformers: {e}. Falling back to simple string matching.")
        return None

try:
    from sentence_transformers import util
    HAS_SEMANTIC = True
except ImportError:
    HAS_SEMANTIC = False
    from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

STOP_WORDS = {
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "by", "with",
    "is", "are", "will", "be", "this", "that", "it", "and", "or", "not",
    "do", "does", "did", "has", "have", "had", "was", "were", "been",
    "can", "could", "would", "should", "may", "might", "shall",
    "before", "after", "during", "between", "from", "into", "about",
    "yes", "no", "market", "contract", "event", "question",
}

GENERIC_KEYWORDS = {
    "price", "above", "below", "over", "under", "more", "less", "than",
    "end", "year", "2025", "2026", "2027", "next", "win", "winner",
    "rate", "percent", "number", "total", "average", "many", "much",
    "day", "week", "month", "january", "february", "march", "april",
    "may", "june", "july", "august", "september", "october", "november", "december",
    "first", "second", "third", "last", "any", "new", "most", "least",
}


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', ' ', text)
    words = [w for w in text.split() if w not in STOP_WORDS and len(w) > 1]
    return ' '.join(words)


def extract_keywords(text: str) -> set:
    normalized = normalize_text(text)
    return set(normalized.split())


def extract_significant_keywords(text: str) -> set:
    normalized = normalize_text(text)
    return set(w for w in normalized.split() if w not in GENERIC_KEYWORDS)


def compute_similarity(title_a: str, title_b: str) -> Tuple[float, str]:
    model = get_model()
    keywords_a = extract_keywords(title_a)
    keywords_b = extract_keywords(title_b)
    intersection = keywords_a & keywords_b
    union = keywords_a | keywords_b
    jaccard = len(intersection) / len(union) if union else 0
    
    # If semantic is available, combine it
    if model and HAS_SEMANTIC:
        emb1 = model.encode(title_a, convert_to_tensor=True)
        emb2 = model.encode(title_b, convert_to_tensor=True)
        cosine_score = util.cos_sim(emb1, emb2).item()
        score_percent = cosine_score * 100
        
        # Weighted average: 70% semantic, 30% keyword
        combined = (score_percent * 0.7 + jaccard * 30) 
        
        reason_parts = [f"Semantic: {score_percent:.0f}%"]
        if intersection:
            reason_parts.append(f"Keywords: {', '.join(sorted(list(intersection))[:3])}")
        
        return combined, " | ".join(reason_parts)
    else:
        # Fallback to Jaccard / Sequence Matcher
        norm_a = normalize_text(title_a)
        norm_b = normalize_text(title_b)
        seq_score = SequenceMatcher(None, norm_a, norm_b).ratio()
        
        combined = (seq_score * 0.4 + jaccard * 0.6) * 100
        
        reason_parts = []
        if intersection:
            reason_parts.append(f"Matching: {', '.join(sorted(list(intersection))[:3])}")
        else:
            reason_parts.append("Sequence similarity")
            
        return combined, " | ".join(reason_parts)


def compute_similarity_fast(keywords_a: set, keywords_b: set) -> float:
    # If ML model exists, skip keyword fast-pass and force deep semantic calculation
    if get_model():
        return 1.0 
        
    if not keywords_a or not keywords_b:
        return 0
    intersection = keywords_a & keywords_b
    union = keywords_a | keywords_b
    return len(intersection) / len(union) if union else 0


def estimate_fee(platform: str, price: float) -> float:
    p = platform.lower()
    if p == "kalshi":
        # Kalshi taker fee: 0.07 * p * (1-p) per contract, capped at $0.0175
        return min(0.07 * price * (1 - price), 0.0175)
    elif p == "manifold":
        return 0.07 * price * (1 - price)
    elif p == "predictit":
        profit_fee = max(0, (1.0 - price)) * 0.10
        withdrawal_fee = max(0, (1.0 - price)) * 0.05
        return profit_fee + withdrawal_fee
    elif p == "polymarket":
        return price * 0.001
    elif p == "ibkr":
        # IBKR ForecastEx: $0.01 fixed fee per contract
        return 0.01
    return 0.0


def compute_pair_arb(market_a: Dict, market_b: Dict) -> Dict[str, Any]:
    yes_a, yes_b = market_a["yesPrice"], market_b["yesPrice"]

    cost1 = yes_a + (1.0 - yes_b)
    fee1_a = estimate_fee(market_a["platform"], yes_a)
    fee1_b = estimate_fee(market_b["platform"], 1.0 - yes_b)
    total1 = cost1 + fee1_a + fee1_b
    roi1 = ((1.0 - total1) / total1) * 100 if total1 < 1.0 else 0

    cost2 = (1.0 - yes_a) + yes_b
    fee2_a = estimate_fee(market_a["platform"], 1.0 - yes_a)
    fee2_b = estimate_fee(market_b["platform"], yes_b)
    total2 = cost2 + fee2_a + fee2_b
    roi2 = ((1.0 - total2) / total2) * 100 if total2 < 1.0 else 0

    if roi1 >= roi2:
        legs = [
            {"platform": market_a["platform"], "marketId": market_a["id"], "title": market_a["title"],
             "side": "YES", "price": round(yes_a, 4), "fee": round(fee1_a, 4),
             "volume": market_a.get("volume", 0), "marketUrl": market_a.get("marketUrl"), "allocation": 1.0},
            {"platform": market_b["platform"], "marketId": market_b["id"], "title": market_b["title"],
             "side": "NO", "price": round(1.0 - yes_b, 4), "fee": round(fee1_b, 4),
             "volume": market_b.get("volume", 0), "marketUrl": market_b.get("marketUrl"), "allocation": 1.0},
        ]
        return {"roi": roi1, "cost": total1, "grossCost": cost1, "fees": fee1_a + fee1_b, "legs": legs, "scenario": 1}
    else:
        legs = [
            {"platform": market_a["platform"], "marketId": market_a["id"], "title": market_a["title"],
             "side": "NO", "price": round(1.0 - yes_a, 4), "fee": round(fee2_a, 4),
             "volume": market_a.get("volume", 0), "marketUrl": market_a.get("marketUrl"), "allocation": 1.0},
            {"platform": market_b["platform"], "marketId": market_b["id"], "title": market_b["title"],
             "side": "YES", "price": round(yes_b, 4), "fee": round(fee2_b, 4),
             "volume": market_b.get("volume", 0), "marketUrl": market_b.get("marketUrl"), "allocation": 1.0},
        ]
        return {"roi": roi2, "cost": total2, "grossCost": cost2, "fees": fee2_a + fee2_b, "legs": legs, "scenario": 2}


def _build_keyword_index(markets: List[Dict[str, Any]]) -> Dict[str, List[int]]:
    index = defaultdict(list)
    for i, m in enumerate(markets):
        keywords = extract_keywords(m["title"])
        for kw in keywords:
            index[kw].append(i)
    return index


def find_arbitrage_pairs(
    markets: List[Dict[str, Any]],
    min_similarity: float = 40.0,
    enabled_platforms: List[str] = None,
    on_progress: Any = None,
) -> List[Dict[str, Any]]:
    if enabled_platforms:
        platform_set = set(p.lower() for p in enabled_platforms)
        markets = [m for m in markets if m["platform"].lower() in platform_set]

    by_platform: Dict[str, List[Dict[str, Any]]] = {}
    for m in markets:
        # Strict user requirement: Must be exactly 2 outcomes (Binary Yes/No)
        if not m.get("isBinary", True) or m.get("outcomeCount", 2) != 2:
            continue
        by_platform.setdefault(m["platform"], []).append(m)

    platforms = list(by_platform.keys())

    total_brute = 0
    for i in range(len(platforms)):
        for j in range(i + 1, len(platforms)):
            total_brute += len(by_platform[platforms[i]]) * len(by_platform[platforms[j]])
    
    logger.info(f"Matcher: {len(platforms)} platforms, {sum(len(v) for v in by_platform.values())} markets, {total_brute:,} brute-force pairs")

    pairs = []
    completed = 0

    model = get_model()
    if model:
        logger.info("Using deep semantic ML embeddings for matching...")
        # Pre-compute embeddings for each platform
        plat_embeddings = {}
        for plat in platforms:
            titles = [m["title"] for m in by_platform[plat]]
            plat_embeddings[plat] = model.encode(titles, convert_to_tensor=True) if titles else None

        total_comparisons = total_brute
        if on_progress:
            on_progress(0, total_comparisons, 0)

        for i in range(len(platforms)):
            pa = platforms[i]
            emb_a = plat_embeddings[pa]
            if emb_a is None: continue
            
            for j in range(i + 1, len(platforms)):
                pb = platforms[j]
                emb_b = plat_embeddings[pb]
                if emb_b is None: continue

                # Matrix multiply for cosine similarity
                cosine_scores = util.cos_sim(emb_a, emb_b)
                threshold_tensor = min_similarity / 100.0
                
                for idx_a in range(len(emb_a)):
                    for idx_b in range(len(emb_b)):
                        completed += 1
                        score = cosine_scores[idx_a][idx_b].item()
                        
                        if score >= threshold_tensor:
                            ma = by_platform[pa][idx_a]
                            mb = by_platform[pb][idx_b]
                            
                            score_percent = score * 100
                            reason = "semantic_high" if score_percent >= 80 else "semantic_medium" if score_percent >= 65 else "semantic_low"
                            arb_data = compute_pair_arb(ma, mb)
                            
                            if arb_data["roi"] > -100:
                                pairs.append({
                                    "marketA": ma,
                                    "marketB": mb,
                                    "roi": arb_data["roi"],
                                    "cost": arb_data["cost"],
                                    "grossCost": arb_data["grossCost"],
                                    "fees": arb_data["fees"],
                                    "matchScore": round(score_percent, 1),
                                    "matchReason": reason,
                                    "opportunityScore": min(100, round((arb_data["roi"] * 10) + (score_percent * 0.5))),
                                    "legs": arb_data["legs"],
                                    "scenario": arb_data["scenario"],
                                })
                        
                        if on_progress and completed % 50000 == 0:
                            on_progress(completed, total_comparisons, len(pairs))
    else:
        all_keywords_cache = {}
        sig_keywords_cache = {}
        for plat in platforms:
            for idx, m in enumerate(by_platform[plat]):
                key = (plat, idx)
                all_keywords_cache[key] = extract_keywords(m["title"])
                sig_keywords_cache[key] = extract_significant_keywords(m["title"])
    
        logger.info(f"Using keyword index with significance filtering...")
    
        candidate_pairs = set()
        for i in range(len(platforms)):
            for j in range(i + 1, len(platforms)):
                pa, pb = platforms[i], platforms[j]
                markets_b = by_platform[pb]
    
                sig_index_b = defaultdict(list)
                for idx_b in range(len(markets_b)):
                    for kw in sig_keywords_cache[(pb, idx_b)]:
                        sig_index_b[kw].append(idx_b)
    
                for idx_a in range(len(by_platform[pa])):
                    sig_kw_a = sig_keywords_cache[(pa, idx_a)]
                    if not sig_kw_a:
                        continue
                    matched_b_indices = set()
                    for kw in sig_kw_a:
                        if kw in sig_index_b:
                            matched_b_indices.update(sig_index_b[kw])
                    for idx_b in matched_b_indices:
                        candidate_pairs.add((pa, pb, idx_a, idx_b))
    
        total_comparisons = len(candidate_pairs)
        logger.info(f"Keyword index reduced to {total_comparisons:,} candidate pairs (from {total_brute:,})")
    
        if on_progress:
            on_progress(0, total_comparisons, 0)
    
        MIN_JACCARD_PREFILTER = 0.30
    
        pairs = []
        completed = 0
        skipped_jaccard = 0
    
        for pa, pb, idx_a, idx_b in candidate_pairs:
            completed += 1
    
            kw_a = all_keywords_cache[(pa, idx_a)]
            kw_b = all_keywords_cache[(pb, idx_b)]
    
            jaccard = compute_similarity_fast(kw_a, kw_b)
            if jaccard < MIN_JACCARD_PREFILTER:
                skipped_jaccard += 1
                if on_progress and completed % 50000 == 0:
                    on_progress(completed, total_comparisons, len(pairs))
                continue
    
            ma = by_platform[pa][idx_a]
            mb = by_platform[pb][idx_b]
    
            sim_score, reason = compute_similarity(ma["title"], mb["title"])
            if sim_score < min_similarity:
                if on_progress and completed % 50000 == 0:
                    on_progress(completed, total_comparisons, len(pairs))
                continue
    
            arb = compute_pair_arb(ma, mb)
    
            end_dates = [d for d in [ma.get("endDate"), mb.get("endDate")] if d]
            earliest_resolution = min(end_dates) if end_dates else None
    
            pair = {
                "comboType": "pair",
                "legCount": 2,
                "legs": arb["legs"],
                "marketA": ma,
                "marketB": mb,
                "combinedYesCost": round(arb["grossCost"], 4),
                "totalCost": round(arb["cost"], 4),
                "fees": round(arb["fees"], 4),
                "potentialProfit": round(max(0, 1.0 - arb["cost"]), 4),
                "roi": round(arb["roi"], 2),
                "matchScore": round(float(sim_score), 1),
                "matchReason": reason,
                "earliestResolution": earliest_resolution,
                "scenario": arb["scenario"],
            }
            pairs.append(pair)
    
            if on_progress and completed % 50000 == 0:
                on_progress(completed, total_comparisons, len(pairs))
    
        if on_progress:
            on_progress(total_comparisons, total_comparisons, len(pairs))
    
        logger.info(f"Matcher: {skipped_jaccard:,} skipped by Jaccard pre-filter, {len(pairs)} pairs found")
    
        pairs.sort(key=lambda p: (
            -p["roi"],
            -p["matchScore"],
            p["earliestResolution"] or "9999-12-31",
        ))
    
        return pairs
