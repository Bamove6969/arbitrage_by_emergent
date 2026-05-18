"""
Parallel LLM Worker System - Processes matches with Gemma4 and Qwen3.5
Splits 2000 matches into 2 batches of 1000, runs 2 workers per model (4 total)
Uses natural language to identify identically-worded questions across platforms
"""
import asyncio
import logging
import os
import re
from typing import List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor
import httpx

MATCH_YES_RE = re.compile(r"^MATCH:\s*YES", re.MULTILINE | re.IGNORECASE)
EXPLANATION_RE = re.compile(r"^Explanation:\s*(.+?)(?:\n\s*\n|\Z)", re.MULTILINE | re.IGNORECASE | re.DOTALL)

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = "http://localhost:11434"

class LLMWorker:
    """
    Individual LLM worker that processes a batch of matches
    """
    
    def __init__(self, worker_id: str, model_name: str, batch_size: int = 1000):
        self.worker_id = worker_id
        self.model_name = model_name
        self.batch_size = batch_size
        self.ollama_url = f"{OLLAMA_BASE_URL}/api/generate"
        self.processed_count = 0
        self.matches_found = 0
        
    async def generate_prompt(self, market_a: Dict, market_b: Dict) -> str:
        """
        Generate natural language prompt for question comparison
        Focuses on semantic meaning, catches subtle differences, rejects multi-outcome questions
        """
        prompt = f"""You are an expert at analyzing prediction market questions to determine if they are asking EXACTLY the same thing, even when worded completely differently.

## Market A ({market_a['platform']}):
**Question:** "{market_a['title']}"
**Yes Price:** {market_a['yesPrice']}
**URL:** {market_a.get('url', 'N/A')}

## Market B ({market_b['platform']}):
**Question:** "{market_b['title']}"
**Yes Price:** {market_b['yesPrice']}
**URL:** {market_b.get('url', 'N/A')}

## Analysis Steps (think through these carefully):

1. **Core Event/Outcome**: What specific event or outcome is each question asking about? Are they identical?

2. **Resolution Conditions**: What exact conditions must be met for "Yes" to resolve in each market? Are they the same?

3. **Timeline/Deadline**: Do both questions have the exact same timeframe or deadline?

4. **Threshold Values**: If numbers are involved (percentages, counts, scores), are the thresholds identical?

5. **Subtle Wording Differences**: Look for words like "exactly", "at least", "more than", "less than", "by", "before", "after" - do they change the meaning?

6. **Binary Check**: Are BOTH questions truly binary (only 2 possible outcomes: Yes/No)? If either question allows 3+ outcomes, they CANNOT match.

7. **Edge Cases**: Consider borderline scenarios - would there be any situation where one market resolves differently than the other?

## Decision Rules:

**MATCH: YES** - ONLY if:
- Both questions ask about the EXACT same event/outcome
- Resolution conditions are identical (no subtle differences)
- Timelines/deadlines match exactly
- BOTH are strictly binary (Yes/No only)
- Any reasonable person would agree they're asking the same thing

**MATCH: NO** - if ANY of these are true:
- Different events or outcomes
- Different resolution conditions (even slightly)
- Different timelines or deadlines
- Different numerical thresholds
- Either question has 3+ possible outcomes
- One is about "exactly X" and the other is "at least X"
- Any ambiguity or potential for different resolution

## Your Response Format:

If EXACTLY the same (semantically identical):
```
MATCH: YES
Explanation: [1-2 sentences explaining why they're identical despite wording differences]
```

If there are ANY differences:
```
MATCH: NO
Explanation: [Clearly state the specific difference - e.g., "Market A asks about 'at least 50%' while Market B asks about 'more than 50%'", or "Market A has deadline of March 2025, Market B is December 2025"]
```

Your response:"""
        return prompt
    
    async def query_ollama(self, prompt: str) -> Tuple[bool, str]:
        """Query Ollama API for match verification"""
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    self.ollama_url,
                    json={
                        "model": self.model_name,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.1,
                            "num_predict": 256
                        }
                    }
                )
                response.raise_for_status()
                result = response.json()
                output = result.get("response", "")
                
                is_match = bool(MATCH_YES_RE.search(output))
                explanation_match = EXPLANATION_RE.search(output)
                if explanation_match:
                    explanation = explanation_match.group(1).strip().strip("`").strip()
                else:
                    explanation = output.strip().strip("`").strip()
                return is_match, explanation
                
        except Exception as e:
            logger.error(f"{self.worker_id} - Ollama query failed: {e}")
            return False, f"Error: {str(e)}"
    
    async def process_pair(self, pair: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single market pair with strict binary filtering"""
        market_a = pair["marketA"]
        market_b = pair["marketB"]
        
        # Pre-filter: Check both markets are strictly binary
        binary_check = self._verify_binary_pair(market_a, market_b)
        if not binary_check['is_valid']:
            self.processed_count += 1
            return {
                "marketA": market_a,
                "marketB": market_b,
                "isMatch": False,
                "explanation": f"REJECTED: {binary_check['reason']}",
                "model": self.model_name,
                "worker": self.worker_id,
                "originalROI": pair["roi"],
                "matchScore": pair.get("matchScore", 0),
                "rejected_reason": binary_check['reason']
            }
        
        # Proceed with LLM analysis
        prompt = await self.generate_prompt(market_a, market_b)
        is_match, explanation = await self.query_ollama(prompt)
        
        self.processed_count += 1
        if is_match:
            self.matches_found += 1
        
        return {
            "marketA": market_a,
            "marketB": market_b,
            "isMatch": is_match,
            "explanation": explanation,
            "model": self.model_name,
            "worker": self.worker_id,
            "originalROI": pair["roi"],
            "matchScore": pair.get("matchScore", 0)
        }
    
    def _verify_binary_pair(self, market_a: Dict, market_b: Dict) -> Dict[str, Any]:
        """
        Verify both markets are strictly binary (2 outcomes only)
        Returns validation result and reason if rejected
        """
        # Check outcome count
        if market_a.get('outcomeCount', 2) != 2:
            return {
                'is_valid': False,
                'reason': f"Market A has {market_a.get('outcomeCount', 'unknown')} outcomes (must be exactly 2)"
            }
        
        if market_b.get('outcomeCount', 2) != 2:
            return {
                'is_valid': False,
                'reason': f"Market B has {market_b.get('outcomeCount', 'unknown')} outcomes (must be exactly 2)"
            }
        
        # Check for multi-outcome keywords in titles
        multi_outcome_keywords = [
            'how many', 'how much', 'what number', 'what percentage',
            'which candidate', 'who will', 'margin of', 'by how much',
            'range of', 'between', 'spread', 'total number',
            'exactly', 'precisely', 'number of', 'count of'
        ]
        
        for market in [market_a, market_b]:
            title = market.get('title', '').lower()
            for keyword in multi_outcome_keywords:
                if keyword in title:
                    # Allow binary over/under
                    if 'over' in title and 'or' in title and 'under' in title:
                        continue
                    return {
                        'is_valid': False,
                        'reason': f"Non-binary question structure detected: '{keyword}' in title"
                    }
        
        # Check for binary question indicators
        binary_indicators = ['will ', 'does ', 'is ', 'are ', 'was ', 'were ', 'whether', 'if ']
        
        for name, market in [('Market A', market_a), ('Market B', market_b)]:
            title = market.get('title', '').lower()
            has_binary = any(ind in title for ind in binary_indicators)
            if not has_binary:
                return {
                    'is_valid': False,
                    'reason': f"{name} doesn't have binary question structure"
                }
        
        return {'is_valid': True, 'reason': None}
    
    async def process_batch(self, pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process a batch of pairs"""
        logger.info(f"{self.worker_id} - Processing batch of {len(pairs)} pairs with {self.model_name}")
        
        tasks = [self.process_pair(pair) for pair in pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out exceptions and log them
        valid_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"{self.worker_id} - Pair {i} failed: {result}")
            else:
                valid_results.append(result)
        
        logger.info(f"{self.worker_id} - Completed: {len(valid_results)} pairs, {self.matches_found} matches found")
        return valid_results


class ParallelLLMProcessor:
    """
    Orchestrates 4 parallel workers (2 per model) processing 2000 matches
    """
    
    def __init__(self):
        self.workers = []
        self.results = []
        
    def setup_workers(self):
        model_name = os.environ.get("OLLAMA_MODEL", "gemma4:31b-cloud")
        self.workers = [
            LLMWorker("gemma-worker-1", model_name),
            LLMWorker("gemma-worker-2", model_name),
        ]
        logger.info(f"Initialized {len(self.workers)} workers with model {model_name}")

    def split_batches(self, pairs: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        mid = len(pairs) // 2
        batches = [pairs[:mid], pairs[mid:]]
        logger.info(f"Split {len(pairs)} pairs into 2 batches: {[len(b) for b in batches]}")
        return batches
    
    async def process_all(self, pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Process all pairs in parallel across 4 workers
        """
        if not self.workers:
            self.setup_workers()
        
        batches = self.split_batches(pairs)
        
        # Assign batches to workers
        tasks = []
        for i, worker in enumerate(self.workers):
            if i < len(batches):
                task = worker.process_batch(batches[i])
                tasks.append(task)
        
        logger.info("Starting parallel processing...")
        start_time = asyncio.get_event_loop().time()
        
        # Run all workers in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        elapsed = asyncio.get_event_loop().time() - start_time
        logger.info(f"Parallel processing completed in {elapsed:.2f}s")
        
        # Combine results
        all_results = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Worker failed: {result}")
            else:
                all_results.extend(result)
        
        # Filter to only exact matches
        exact_matches = [r for r in all_results if r.get("isMatch", False)]
        
        logger.info(f"Total processed: {len(all_results)}, Exact matches: {len(exact_matches)}")
        
        self.results = exact_matches
        return exact_matches
    
    def get_stats(self) -> Dict[str, Any]:
        """Get processing statistics"""
        return {
            "total_workers": len(self.workers),
            "workers": [
                {
                    "id": w.worker_id,
                    "model": w.model_name,
                    "processed": w.processed_count,
                    "matches_found": w.matches_found
                }
                for w in self.workers
            ],
            "total_results": len(self.results)
        }


async def run_parallel_llm_verification(pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Main entry point - Process pairs with parallel LLM workers
    """
    processor = ParallelLLMProcessor()
    return await processor.process_all(pairs)


if __name__ == "__main__":
    # Test with sample data
    import json
    
    async def test():
        # Load sample pairs
        with open("/tmp/test_pairs.json", "r") as f:
            pairs = json.load(f)
        
        processor = ParallelLLMProcessor()
        results = await processor.process_all(pairs[:2000])
        
        print(f"\nResults: {len(results)} exact matches found")
        print(f"Stats: {processor.get_stats()}")
    
    # asyncio.run(test())
    pass
