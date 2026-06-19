"""
Final LLM verification stage — local Ollama -> cloud model (gemma4:31b-cloud).

The 2000 fuzzy matches arriving from the Kaggle GPU notebook are split into
2 batches of 1000. Each batch is handled by one "instance" of the cloud model
with 2 workers each (2 instances x 2 workers = 4 concurrent requests through
the local Ollama daemon, which proxies -cloud models to ollama.com).

Verification semantics (strict):
  - 100% MEANING match required — wording may differ completely, meaning may not.
  - Pairs that look nearly identical but differ in any resolution-relevant
    detail (deadline, threshold, subject, scope) are REJECTED.
  - Both questions must be binary (exactly 2 outcomes) — non-binary pairs are
    REJECTED here as a double-check (primary filter runs in the notebook).
  - Pairs flagged `inverted` are verified as opposite-resolution pairs.
"""
import asyncio
import logging
import json
import os
import re
from typing import List, Dict, Any

import httpx

from backend.live_state import (
    reset_llm_state, finish_llm_state, worker_start, worker_done,
)

logger = logging.getLogger(__name__)

OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b-cloud")
# One model per instance (comma-separated). Two DIFFERENT models decorrelate
# errors: a blind spot in one doesn't poison both halves of the 2000.
# deepseek-v4-flash needs a paid sub; gpt-oss:120b + gemma4:31b are free-tier.
OLLAMA_VERIFY_MODELS = [m.strip() for m in os.environ.get(
    "OLLAMA_VERIFY_MODELS", "gpt-oss:120b-cloud,gemma4:31b-cloud").split(",") if m.strip()]
NUM_INSTANCES        = int(os.environ.get("LLM_VERIFY_INSTANCES", "2"))
WORKERS_PER_INSTANCE = int(os.environ.get("LLM_VERIFY_WORKERS", "2"))
MIN_CONFIDENCE       = int(os.environ.get("LLM_VERIFY_MIN_CONF", "85"))

_PROMPT_SAME = """You are verifying prediction-market arbitrage pairs. Two questions from different markets are a MATCH only if they resolve identically in MEANING — the wording can be totally different, but the meaning must match 100%. If they look similar but ANY resolution-relevant detail differs (deadline, threshold number, subject, scope), they are NOT a match.

Also verify BOTH questions are binary (exactly 2 possible outcomes, e.g. Yes/No or CandidateA/CandidateB). If either has 1 or 3+ outcomes, answer NO.

Question A: "{a}"
Question B: "{b}"

Reply in EXACTLY this format:
MATCH: YES or NO
CONFIDENCE: 0-100
REASON: one sentence"""

_PROMPT_INVERTED = """You are verifying prediction-market arbitrage pairs. These two questions are believed to be INVERTED: question A should resolve YES exactly when question B resolves NO (same real-world event, opposite directions). Confirm this holds 100% in MEANING. If ANY resolution-relevant detail differs (deadline, threshold, subject, scope), answer NO.

Also verify BOTH questions are binary (exactly 2 possible outcomes). If either has 1 or 3+ outcomes, answer NO.

Question A: "{a}"
Question B: "{b}"

Reply in EXACTLY this format:
MATCH: YES or NO
CONFIDENCE: 0-100
REASON: one sentence"""


async def _ollama_chat(client: httpx.AsyncClient, prompt: str, model: str) -> str:
    """One chat call through the local Ollama daemon (proxies -cloud models)."""
    r = await client.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=120.0,
    )
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "")


def _parse_verdict(text: str) -> Dict[str, Any]:
    up = text.upper()
    m = re.search(r"MATCH[:\s]*\b(YES|NO)\b", up)
    is_match = bool(m and m.group(1) == "YES")
    c = re.search(r"CONFIDENCE[:\s]*(\d+)", up)
    confidence = int(c.group(1)) if c else (90 if is_match else 10)
    reason = ""
    rm = re.search(r"REASON[:\s]*(.+)", text, re.IGNORECASE | re.DOTALL)
    if rm:
        reason = rm.group(1).strip()[:200]
    return {"is_exact_match": is_match, "confidence": confidence, "reasoning": reason}


async def verify_match_with_llm(client: httpx.AsyncClient, match: Dict[str, Any],
                                model: str = OLLAMA_MODEL) -> Dict[str, Any]:
    title_a = match.get("marketA", {}).get("title", "")
    title_b = match.get("marketB", {}).get("title", "")
    template = _PROMPT_INVERTED if match.get("inverted") else _PROMPT_SAME
    prompt = template.format(a=title_a[:300], b=title_b[:300])
    try:
        text = await _ollama_chat(client, prompt, model)
        return {**match, **_parse_verdict(text), "verifyModel": model}
    except Exception as e:
        logger.warning(f"LLM verification failed: {e}")
        return {**match, "is_exact_match": False, "confidence": 0,
                "reasoning": str(e)[:200], "verifyModel": model}


async def _run_instance(instance_id: int, batch: List[Dict[str, Any]],
                        model: str, workers: int) -> List[Dict[str, Any]]:
    """One model instance: its own semaphore = workers concurrent requests."""
    sem = asyncio.Semaphore(workers)
    done = 0

    async def one(client, m):
        nonlocal done
        async with sem:
            worker_start(instance_id)
            r = await verify_match_with_llm(client, m, model)
            is_exact = bool(r.get("is_exact_match")) and r.get("confidence", 0) >= MIN_CONFIDENCE
            worker_done(instance_id, is_exact)
            done += 1
            if done % 100 == 0:
                logger.info(f"[instance {instance_id}] {done}/{len(batch)} verified")
            return r

    async with httpx.AsyncClient() as client:
        return list(await asyncio.gather(*[one(client, m) for m in batch]))


async def verify_matches_with_llm(matches: List[Dict[str, Any]],
                                  models: List[str] = None,
                                  num_instances: int = NUM_INSTANCES,
                                  workers_per_instance: int = WORKERS_PER_INSTANCE,
                                  ) -> List[Dict[str, Any]]:
    """Split matches across instances (2000 -> 2 x 1000), one model per
    instance, and verify in parallel."""
    if not matches:
        return []
    models = models or OLLAMA_VERIFY_MODELS or [OLLAMA_MODEL]
    logger.info(
        f"LLM verification: {len(matches)} matches | models={models} | "
        f"{num_instances} instances x {workers_per_instance} workers")

    # contiguous split: 2000 -> [0:1000], [1000:2000]
    size = (len(matches) + num_instances - 1) // num_instances
    batches = [matches[i:i + size] for i in range(0, len(matches), size)]

    # publish the live picture for the OLLAMA tab before we start crunching
    reset_llm_state(
        len(matches),
        [(models[i % len(models)], len(b), workers_per_instance)
         for i, b in enumerate(batches)],
    )

    try:
        results_nested = await asyncio.gather(*[
            _run_instance(i, b, models[i % len(models)], workers_per_instance)
            for i, b in enumerate(batches)
        ])
    finally:
        finish_llm_state()
    verified = [r for batch in results_nested for r in batch]

    exact_matches = [
        v for v in verified
        if v.get("is_exact_match", False) and v.get("confidence", 0) >= MIN_CONFIDENCE
    ]

    for m in exact_matches:
        end_a = m.get("marketA", {}).get("endDate") or "9999-12-31"
        end_b = m.get("marketB", {}).get("endDate") or "9999-12-31"
        m["earliestEndDate"] = min(end_a, end_b)
    exact_matches.sort(key=lambda x: (x["earliestEndDate"], -x.get("roi", 0)))

    logger.info(f"LLM verification complete. {len(exact_matches)} exact matches out of {len(matches)}")
    return exact_matches


def get_llm_verified_matches() -> List[Dict[str, Any]]:
    """Returns cached LLM-verified matches."""
    return _llm_verified_matches


_llm_verified_matches: List[Dict[str, Any]] = []
_verification_lock = asyncio.Lock()


async def run_llm_verification(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Main entry point to run LLM verification on matches."""
    global _llm_verified_matches

    async with _verification_lock:
        verified = await verify_matches_with_llm(matches)
        _llm_verified_matches = verified
        return verified
