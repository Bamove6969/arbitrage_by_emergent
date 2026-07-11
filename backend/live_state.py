"""Live state holder for LLM verification progress (was untracked in the repo)."""
from datetime import datetime
from typing import Any, Dict, List, Optional

_llm_state: Dict[str, Any] = {
    "running": False,
    "received": 0,
    "total_exact": 0,
    "started_at": None,
    "finished_at": None,
    "instances": [],
}


def get_llm_state() -> Dict[str, Any]:
    return {**_llm_state, "instances": [dict(i) for i in _llm_state["instances"]]}


def reset_llm_state(received: int, models: Optional[List[str]] = None, workers: int = 2, totals: Optional[List[int]] = None):
    models = models or []
    totals = totals or [0] * len(models)
    _llm_state.update({
        "running": True,
        "received": received,
        "total_exact": 0,
        "started_at": datetime.utcnow().isoformat(),
        "finished_at": None,
        "instances": [
            {"model": m, "workers": workers, "active": 0, "done": 0,
             "exact": 0, "total": t, "remaining": t}
            for m, t in zip(models, totals)
        ],
    })


def worker_start(instance_idx: int):
    try:
        _llm_state["instances"][instance_idx]["active"] += 1
    except IndexError:
        pass


def worker_done(instance_idx: int, is_exact: bool):
    try:
        inst = _llm_state["instances"][instance_idx]
        inst["active"] = max(0, inst["active"] - 1)
        inst["done"] += 1
        inst["remaining"] = max(0, inst["total"] - inst["done"])
        if is_exact:
            inst["exact"] += 1
            _llm_state["total_exact"] += 1
    except IndexError:
        pass


def finish_llm_state():
    _llm_state["running"] = False
    _llm_state["finished_at"] = datetime.utcnow().isoformat()


def set_verification_progress(received: int, done: int, exact: int, model: str, workers: int):
    _llm_state["running"] = done < received
    _llm_state["received"] = received
    _llm_state["total_exact"] = exact
    if not _llm_state["instances"]:
        _llm_state["instances"] = [{
            "model": model, "workers": workers, "active": 0, "done": 0,
            "exact": 0, "total": received, "remaining": received,
        }]
    inst = _llm_state["instances"][0]
    inst.update({"model": model, "workers": workers, "done": done, "exact": exact,
                 "total": received, "remaining": max(0, received - done)})
    if done >= received and received > 0:
        finish_llm_state()
