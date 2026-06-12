"""
Parallel LLM Verification — 2 instances × 2 workers = 4 concurrent slots.

Each pair goes through two checks:
  1. Hard binary filter  — market data must show outcomeCount == 2 for BOTH sides.
     Anything with 1 or 3+ outcomes is disqualified immediately, no LLM call needed.
  2. Semantic equivalence — HuggingFace Inference API (HF_MODEL, 4 concurrent slots)
     reads the two questions and decides whether they are asking about EXACTLY the same
     real-world event/outcome.
     Questions that look identical but have different resolution conditions are rejected.
     Questions that look different but mean the same thing are approved.

The 2000-pair limit is intentional for now. If LLM verification finds very few matches
that limit can be raised in the Kaggle notebook (top_k param) without any code change here.
"""

import asyncio
import logging
import os
import re
from typing import List, Dict, Tuple

import httpx

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
HF_TOKEN  = os.environ.get("HF_TOKEN", "")
HF_MODEL  = os.environ.get("HF_MODEL", "google/gemma-3-27b-it")
HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}/v1/chat/completions"

# 4 concurrent slots — HF serverless handles parallelism server-side;
# the semaphore prevents thundering-herd on the API.
_SEMAPHORE: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(4)
    return _SEMAPHORE


# ── Binary pre-filter (no LLM, pure metadata) ──────────────────────────────────

def _is_binary(market: Dict) -> Tuple[bool, str]:
    """
    True only if the market has EXACTLY 2 outcomes.
    1 outcome or 3+ outcomes are both disqualified immediately.
    Examples of valid binary: Yes/No, Trump/Harris, Over/Under.
    Examples of invalid: which of 5 candidates wins, exact vote percentage.
    """
    count = market.get("outcomeCount", 2)
    if count == 1:
        return False, "only 1 outcome"
    if count > 2:
        return False, f"{count} outcomes (only 2-outcome markets are usable)"
    if not market.get("isBinary", True):
        return False, "isBinary=False in market metadata"
    return True, ""


def binary_filter(pair: Dict) -> Tuple[bool, str]:
    """Both sides must independently pass the binary check."""
    ok_a, reason_a = _is_binary(pair["marketA"])
    if not ok_a:
        return False, f"Market A: {reason_a}"
    ok_b, reason_b = _is_binary(pair["marketB"])
    if not ok_b:
        return False, f"Market B: {reason_b}"
    return True, ""


# ── LLM prompt ────────────────────────────────────────────────────────────────

def _build_prompt(market_a: Dict, market_b: Dict) -> str:
    return f"""You are verifying whether two binary prediction-market questions are semantically identical — asking about EXACTLY the same real-world event, with the same resolution conditions, same deadline, and the same two possible outcomes.

Platform A ({market_a['platform']}): "{market_a['title']}"
Platform B ({market_b['platform']}): "{market_b['title']}"

Work through these checks:

1. CORE EVENT — Are both questions about the exact same real-world event or outcome?
2. RESOLUTION — Would both markets resolve YES under precisely the same conditions? Any difference in threshold, scope, or wording that could cause one to resolve YES while the other resolves NO disqualifies the pair.
3. TIMELINE — Do both share the same deadline or time window? Different dates = different questions.
4. SCOPE — Check for subtle modifiers: "at least" vs "more than", "by end of year" vs "before December", named person vs their role.
5. EDGE CASES — Is there any realistic scenario where one resolves YES and the other resolves NO simultaneously?

Reply in EXACTLY this format — nothing else before or after:

MATCH: YES
Explanation: <one sentence explaining why they are identical despite different wording>

or

MATCH: NO
Explanation: <one sentence naming the specific difference that disqualifies the pair>"""


# ── Single pair verification ───────────────────────────────────────────────────

async def _verify_pair(pair: Dict, worker_id: int) -> Dict:
    market_a = pair["marketA"]
    market_b = pair["marketB"]

    # Step 1 — hard binary filter: instant disqualification, no API call
    ok, reason = binary_filter(pair)
    if not ok:
        return {**pair, "isMatch": False,
                "explanation": f"DISQUALIFIED (non-binary): {reason}",
                "worker": worker_id}

    # Step 2 — HuggingFace Inference API semantic equivalence check
    prompt = _build_prompt(market_a, market_b)

    async with _sem():
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    HF_API_URL,
                    headers={"Authorization": f"Bearer {HF_TOKEN}"},
                    json={
                        "model": HF_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 150,
                        "temperature": 0.1,
                    },
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning(f"[worker {worker_id}] HF API call failed: {exc}")
            return {**pair, "isMatch": False,
                    "explanation": f"HF API error: {exc}", "worker": worker_id}

    is_match = bool(re.match(r"MATCH:\s*YES", content, re.IGNORECASE))
    expl_m   = re.search(r"Explanation:\s*(.+)", content, re.IGNORECASE | re.DOTALL)
    explanation = expl_m.group(1).strip() if expl_m else content[:200]

    return {**pair, "isMatch": is_match, "explanation": explanation, "worker": worker_id}


# ── Parallel batch processing ──────────────────────────────────────────────────

async def _process_half(pairs: List[Dict], instance_id: int) -> List[Dict]:
    """One 'instance' processes its half with 2 concurrent workers."""
    tasks = [
        _verify_pair(p, worker_id=instance_id * 2 + (i % 2))
        for i, p in enumerate(pairs)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = []
    for r in results:
        if isinstance(r, Exception):
            logger.error(f"[instance {instance_id}] pair failed: {r}")
        else:
            out.append(r)
    return out


async def run_parallel_llm_verification(pairs: List[Dict]) -> List[Dict]:
    """
    Main entry point.

    Splits pairs into two halves, runs both in parallel (4 concurrent HF API slots),
    and returns ALL results — caller filters on result['isMatch'].

    To raise the ceiling beyond 2000, increase top_k in the Kaggle notebook.
    """
    if not HF_TOKEN:
        logger.error("HF_TOKEN not set — LLM verification skipped")
        return pairs

    logger.info(
        f"LLM verification starting: {len(pairs)} pairs | "
        f"model={HF_MODEL} | 2 instances × 2 workers = 4 concurrent slots"
    )

    mid = len(pairs) // 2
    half_a, half_b = pairs[:mid], pairs[mid:]

    results_a, results_b = await asyncio.gather(
        _process_half(half_a, instance_id=0),
        _process_half(half_b, instance_id=1),
    )

    all_results  = results_a + results_b
    verified     = [r for r in all_results if r.get("isMatch")]
    disqualified = len(all_results) - len(verified)

    logger.info(
        f"LLM verification done: {len(all_results)} processed → "
        f"{len(verified)} verified matches, {disqualified} disqualified"
    )
    return all_results
