"""
HTML Report Generator
Creates a clean, attractive single-page report of confirmed arbitrage matches.
"""
import logging
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _fmt_pct(v: Any) -> str:
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_roi(v: Any) -> str:
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_score(v: Any) -> str:
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_date(v: Any) -> str:
    if not v:
        return "No end date"
    s = str(v)
    # Try to keep just the date part if ISO-like
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt) if "%" not in fmt[-3:] else 19], fmt).strftime("%b %d, %Y")
        except (ValueError, TypeError):
            continue
    return s[:10] if len(s) >= 10 else s


def _platform_chip_classes(platform: str) -> str:
    p = (platform or "").lower()
    if "poly" in p:
        return "bg-indigo-100 text-indigo-800 ring-indigo-200"
    if "predict" in p:
        return "bg-amber-100 text-amber-800 ring-amber-200"
    if "ibkr" in p or "interactive" in p:
        return "bg-rose-100 text-rose-800 ring-rose-200"
    return "bg-slate-100 text-slate-800 ring-slate-200"


def _roi_classes(roi: Any) -> str:
    try:
        r = float(roi)
    except (TypeError, ValueError):
        return "bg-slate-50 text-slate-700 ring-slate-200"
    if r >= 10:
        return "bg-emerald-100 text-emerald-900 ring-emerald-300"
    if r >= 5:
        return "bg-green-100 text-green-900 ring-green-300"
    if r >= 1:
        return "bg-lime-100 text-lime-900 ring-lime-300"
    return "bg-slate-100 text-slate-700 ring-slate-200"


def _esc(s: Any) -> str:
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _sort_key(m: Dict[str, Any]):
    """Rank: match quality DESC -> arbitrage ROI DESC -> soonest end ASC."""
    try:
        score = -float(m.get("confidence", m.get("matchScore", 0)) or 0)
    except (TypeError, ValueError):
        score = 0.0
    try:
        roi = -float(m.get("roi", 0) or 0)
    except (TypeError, ValueError):
        roi = 0.0
    end = m.get("earliestEndDate") or m.get("marketA", {}).get("endDate") or "9999-12-31"
    return (score, roi, str(end))


def _market_block(market: Dict[str, Any], label: str, action: str = "") -> str:
    platform = market.get("platform", "Unknown")
    title = market.get("title", "(no title)")   # full text, never truncated
    yes_p = _fmt_pct(market.get("yesPrice"))
    no_p = _fmt_pct(market.get("noPrice"))
    # the notebook sends `marketUrl`; older payloads used `url`
    url = market.get("marketUrl") or market.get("url")
    end_date = _fmt_date(market.get("endDate"))
    chip = _platform_chip_classes(platform)

    if url:
        # question text itself is the hyperlink to the exact market page
        title_html = (
            f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer" '
            f'class="text-base font-medium text-slate-900 leading-snug underline '
            f'decoration-slate-300 underline-offset-2 hover:decoration-slate-900 '
            f'hover:text-indigo-700 transition">{_esc(title)}</a>'
        )
        platform_html = (
            f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer" '
            f'class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold '
            f'ring-1 hover:opacity-75 transition {chip}">{_esc(platform)} &#8599;</a>'
        )
        button = (
            f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer" '
            f'class="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium '
            f'bg-slate-900 text-white hover:bg-slate-700 transition shadow-sm">'
            f'Verify on {_esc(platform)} <span aria-hidden="true">&#8599;</span></a>'
        )
    else:
        title_html = f'<p class="text-base font-medium text-slate-900 leading-snug">{_esc(title)}</p>'
        platform_html = (
            f'<span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold ring-1 {chip}">'
            f'{_esc(platform)}</span>'
        )
        button = (
            '<button disabled class="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg '
            'text-sm font-medium bg-slate-200 text-slate-400 cursor-not-allowed">'
            f'No link for {_esc(platform)}</button>'
        )

    action_html = (
        f'<div class="text-xs font-semibold px-2 py-1 rounded-md bg-amber-50 text-amber-900 '
        f'ring-1 ring-amber-200 inline-flex w-fit">YOUR TRADE: {_esc(action)}</div>'
    ) if action else ""

    return f"""
      <div class="flex-1 min-w-0 p-5 bg-white rounded-xl ring-1 ring-slate-200 flex flex-col gap-3">
        <div class="flex items-center justify-between gap-2">
          <span class="text-[11px] uppercase tracking-wider font-semibold text-slate-400">{_esc(label)}</span>
          {platform_html}
        </div>
        {title_html}
        <div class="flex items-center gap-2 text-sm">
          <span class="inline-flex items-center px-2 py-1 rounded-md bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200 font-mono">YES {yes_p}</span>
          <span class="inline-flex items-center px-2 py-1 rounded-md bg-rose-50 text-rose-800 ring-1 ring-rose-200 font-mono">NO {no_p}</span>
        </div>
        {action_html}
        <div class="text-xs text-slate-500">Ends: {_esc(end_date)}</div>
        <div class="mt-auto pt-2">{button}</div>
      </div>
    """


def _trade_actions(m: Dict[str, Any]) -> tuple:
    """Per-leg trade instruction derived from the arb scenario.
    1: YES on A + NO on B | 2: NO on A + YES on B | 3 (inverted): YES on both."""
    if m.get("inverted") or m.get("scenario") == 3:
        return ("Buy YES", "Buy YES")
    if m.get("scenario") == 2:
        return ("Buy NO", "Buy YES")
    return ("Buy YES", "Buy NO")


def _match_card(idx: int, m: Dict[str, Any]) -> str:
    a = m.get("marketA", {}) or {}
    b = m.get("marketB", {}) or {}
    roi = m.get("roi")
    confidence = m.get("confidence")
    score = confidence if confidence is not None else m.get("matchScore")
    reasoning = m.get("reasoning") or m.get("explanation") or "No explanation provided."
    model = m.get("verifyModel") or m.get("model") or ""
    inverted = bool(m.get("inverted"))
    roi_cls = _roi_classes(roi)
    earliest = _fmt_date(m.get("earliestEndDate") or a.get("endDate") or b.get("endDate"))
    act_a, act_b = _trade_actions(m)

    inv_badge = (
        '<span class="inline-flex items-center px-3 py-1.5 rounded-lg text-sm font-semibold '
        'bg-violet-50 text-violet-800 ring-1 ring-violet-200">INVERTED PAIR — YES on both sides</span>'
    ) if inverted else ""

    cost = m.get("cost")
    try:
        cost_str = f"{float(cost):.3f}" if cost is not None else "—"
    except (TypeError, ValueError):
        cost_str = "—"

    detail_rows = []
    for k, label in (("biEncoderScore", "Embedding similarity"), ("stage2Score", "Reranker score"),
                     ("stage3Score", "Deep-rerank score"), ("fuzzyScore", "Fuzzy (text) score")):
        if m.get(k) is not None:
            detail_rows.append(f'<div class="flex justify-between"><span class="text-slate-500">{label}</span>'
                               f'<span class="font-mono text-slate-700">{_esc(m.get(k))}</span></div>')
    detail_rows.append(f'<div class="flex justify-between"><span class="text-slate-500">Combined cost of both legs</span>'
                       f'<span class="font-mono text-slate-700">{cost_str}</span></div>')
    if model:
        detail_rows.append(f'<div class="flex justify-between"><span class="text-slate-500">Verified by</span>'
                           f'<span class="font-mono text-slate-700">{_esc(model)}</span></div>')
    details = "\n".join(detail_rows)

    return f"""
    <article class="group bg-gradient-to-br from-white to-slate-50 rounded-2xl ring-1 ring-slate-200 shadow-sm hover:shadow-md hover:ring-slate-300 transition p-5 sm:p-6"
             x-data="{{ open: false }}">
      <header class="flex flex-wrap items-center gap-3 mb-4">
        <span class="text-xs font-semibold text-slate-400">#{idx}</span>
        <span class="inline-flex items-center px-3 py-1.5 rounded-lg text-sm font-bold ring-1 {roi_cls}">
          ROI {_fmt_roi(roi)}
        </span>
        <span class="inline-flex items-center px-3 py-1.5 rounded-lg text-sm font-semibold bg-sky-50 text-sky-800 ring-1 ring-sky-200">
          Match confidence {_fmt_score(score)}%
        </span>
        {inv_badge}
        <span class="ml-auto text-xs text-slate-500">Earliest end: <span class="font-medium text-slate-700">{_esc(earliest)}</span></span>
      </header>

      <div class="flex flex-col md:flex-row gap-4">
        {_market_block(a, "Market A", action=act_a)}
        <div class="flex md:flex-col items-center justify-center text-slate-300 font-bold select-none">
          <span class="text-2xl">{"&#8645;" if inverted else "&harr;"}</span>
        </div>
        {_market_block(b, "Market B", action=act_b)}
      </div>

      <div class="mt-5">
        <button @click="open = !open"
                class="inline-flex items-center gap-1.5 text-sm font-medium text-slate-600 hover:text-slate-900 transition">
          <span x-text="open ? '▾' : '▸'" class="font-mono w-3 inline-block"></span>
          <span>Why these match &middot; full scoring detail</span>
        </button>
        <div x-show="open" x-collapse x-cloak class="mt-3 p-4 bg-slate-50 rounded-lg ring-1 ring-slate-200 text-sm text-slate-700 leading-relaxed">
          <p class="font-medium text-slate-800 mb-2">LLM verdict:</p>
          <p>{_esc(reasoning)}</p>
          <div class="mt-3 pt-3 border-t border-slate-200 grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-1.5 text-xs">
            {details}
          </div>
        </div>
      </div>
    </article>
    """


def generate_html_report(matches: List[Dict[str, Any]], output_path: Optional[str] = None) -> str:
    """Render a clean, self-contained HTML report of confirmed arbitrage matches."""
    matches = list(matches or [])
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(matches)

    if total:
        rois = [float(m.get("roi", 0) or 0) for m in matches]
        scores = [float(m.get("matchScore", 0) or 0) for m in matches]
        avg_roi = sum(rois) / len(rois) if rois else 0.0
        avg_score = sum(scores) / len(scores) if scores else 0.0
        max_roi = max(rois) if rois else 0.0
    else:
        avg_roi = avg_score = max_roi = 0.0

    sorted_matches = sorted(matches, key=_sort_key)

    if not sorted_matches:
        body = """
        <div class="max-w-2xl mx-auto mt-20 text-center">
          <div class="text-6xl mb-4">&#128269;</div>
          <h2 class="text-2xl font-semibold text-slate-800 mb-2">No confirmed matches yet</h2>
          <p class="text-slate-500">When the workers confirm an arbitrage opportunity, it will show up here.</p>
        </div>
        """
    else:
        cards = "\n".join(_match_card(i + 1, m) for i, m in enumerate(sorted_matches))
        body = f'<div class="grid grid-cols-1 gap-5">{cards}</div>'

    stats_html = f"""
      <dl class="grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-4 mt-6">
        <div class="rounded-xl bg-white ring-1 ring-slate-200 px-4 py-3">
          <dt class="text-xs font-medium text-slate-500 uppercase tracking-wider">Matches</dt>
          <dd class="mt-1 text-2xl font-semibold text-slate-900">{total}</dd>
        </div>
        <div class="rounded-xl bg-white ring-1 ring-slate-200 px-4 py-3">
          <dt class="text-xs font-medium text-slate-500 uppercase tracking-wider">Avg ROI</dt>
          <dd class="mt-1 text-2xl font-semibold text-emerald-700">{avg_roi:.2f}%</dd>
        </div>
        <div class="rounded-xl bg-white ring-1 ring-slate-200 px-4 py-3">
          <dt class="text-xs font-medium text-slate-500 uppercase tracking-wider">Max ROI</dt>
          <dd class="mt-1 text-2xl font-semibold text-emerald-700">{max_roi:.2f}%</dd>
        </div>
        <div class="rounded-xl bg-white ring-1 ring-slate-200 px-4 py-3">
          <dt class="text-xs font-medium text-slate-500 uppercase tracking-wider">Avg Match</dt>
          <dd class="mt-1 text-2xl font-semibold text-sky-700">{avg_score:.1f}</dd>
        </div>
      </dl>
    """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Arbitrage Match Report - {generated_at}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/@alpinejs/collapse@3.x.x/dist/cdn.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
  <style>
    [x-cloak] {{ display: none !important; }}
    body {{ font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }}
  </style>
</head>
<body class="bg-slate-100 text-slate-900 min-h-screen">
  <div class="max-w-6xl mx-auto px-4 sm:px-6 py-8 sm:py-10">
    <header class="mb-8">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 class="text-3xl sm:text-4xl font-bold tracking-tight text-slate-900">
            Arbitrage Match Report
          </h1>
          <p class="mt-1 text-sm text-slate-500">
            Confirmed binary exact-matches across prediction markets.
          </p>
        </div>
        <div class="text-right text-sm text-slate-500">
          <div>Generated</div>
          <div class="font-mono text-slate-700">{generated_at}</div>
        </div>
      </div>
      {stats_html}
    </header>

    <main>
      {body}
    </main>

    <footer class="mt-12 pt-6 border-t border-slate-200 text-xs text-slate-400 text-center">
      Arbitrage Calculator &middot; {total} match{'es' if total != 1 else ''} &middot; {generated_at}
    </footer>
  </div>
</body>
</html>
"""

    if output_path:
        try:
            p = Path(output_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(html, encoding="utf-8")
            logger.info(f"HTML report written to {p}")
        except Exception as e:
            logger.error(f"Failed to write HTML report to {output_path}: {e}")

    return html


def get_latest_report_path(reports_dir: str = "/app/reports") -> Optional[str]:
    """Return the path to the most recent arbitrage_report_*.html file, or None."""
    try:
        d = Path(reports_dir)
        if not d.is_dir():
            return None
        candidates = sorted(
            d.glob("arbitrage_report_*.html"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return str(candidates[0]) if candidates else None
    except Exception as e:
        logger.error(f"Failed to locate latest report in {reports_dir}: {e}")
        return None
