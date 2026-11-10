"""
gemini_helper.py — OPTIONAL plain-language explanation of the verdict.

Gemini ONLY rephrases the already-computed structured data into a short, neutral
summary. It NEVER computes or invents numbers. The whole feature is optional and
crash-proof: with no API key, or on ANY error/timeout, explain_verdict() returns
None and the app shows exactly what it does today — no error, no empty box.

Design notes:
- No Streamlit here, so the data layer stays importable/testable on its own. The
  API key is always passed in as a string argument (app.py reads st.secrets).
- google-genai (the current Gemini SDK; it replaced google-generativeai, whose
  support ended) is imported LAZILY, inside _generate, and only after we've
  confirmed there's a key. So importing this module never fails even if the
  package isn't installed/configured, and the no-key path needs no network at all.
"""
import json
import logging

logger = logging.getLogger(__name__)

# If listing models fails, try these names in order (newest-style first).
_FALLBACK_MODELS = ("gemini-flash-latest", "gemini-1.5-flash")

# Keep the call from hanging the UI (deep dives + thinking take a little longer).
_REQUEST_TIMEOUT_S = 45

# Output token budget per depth. IMPORTANT: the current free Flash model
# (gemini-2.5-flash) is a *thinking* model — its internal reasoning tokens count
# against max_output_tokens. (The old google-generativeai SDK couldn't limit
# that; we keep the generous ceilings anyway, they are harmless.) With a small ceiling the budget
# is spent thinking and the visible text truncates mid-sentence (the original
# bug). The reasoning grows with input size, so the ceiling must comfortably
# cover thinking + the actual answer; these generous caps let the model finish
# naturally (finish_reason=STOP) instead of hitting MAX_TOKENS. They are only a
# safety ceiling — the visible answer length is governed by the prompt (one
# paragraph for quick, a few short sections for deep), not by these numbers.
_TOKENS_BY_DEPTH = {"quick": 4096, "deep": 8192}

# Core strict rules, shared by both depths: rephrase only, never invent, no advice.
_CORE_RULES = (
    "You explain a stock analysis to a non-expert using ONLY the already-computed "
    "data provided as JSON.\n"
    "STRICT RULES:\n"
    "- Use ONLY the numbers and labels in the data. NEVER invent, estimate, or "
    "compute any figure that is not present.\n"
    "- Any field whose value is \"MISSING\" is unknown — do not mention it at all.\n"
    "- Describe what the data shows. Do NOT give buy/sell recommendations or advice.\n"
    "- Stay neutral and factual: no hype, no price predictions, no guarantees.\n"
    "- Use plain language a beginner understands, and finish every sentence."
)

# "Quick take": one tight paragraph with the headline reasons.
_QUICK_FORMAT = (
    "FORMAT: Write ONE tight paragraph of about 4-6 sentences giving the headline "
    "reasons the stock scored the way it did. No headings and no bullet lists."
)

# "Deep dive": labeled sections, each 2-4 sentences, omitting all-MISSING ones.
_DEEP_FORMAT = (
    "FORMAT: Write a structured breakdown using ONLY the provided data, with these "
    "short labeled sections in this order (put each label in bold on its own line):\n"
    "**Valuation**, **Growth & profitability**, **Financial health**, "
    "**Technicals & trend**, **Analyst view & the gap**.\n"
    "Keep each section to 2-4 sentences. OMIT entirely any section whose inputs are "
    "all MISSING. End with a final line that starts with '**Bottom line:**' and "
    "sums it up in one sentence."
)


def _build_prompt(structured_data, depth) -> str:
    """Assemble the strict prompt for the requested depth. Tolerant of odd data."""
    data_json = json.dumps(structured_data, indent=2, ensure_ascii=False, default=str)
    fmt = _DEEP_FORMAT if depth == "deep" else _QUICK_FORMAT
    return (f"{_CORE_RULES}\n\n{fmt}\n\nDATA (JSON):\n{data_json}\n\n"
            "Now write the explanation.")


_MODEL_CACHE = {}


def _select_flash_model(client):
    """Ask the API which models this key can use; pick a current Flash model that
    supports generateContent. Returns a model name, or None."""
    if "name" in _MODEL_CACHE:
        return _MODEL_CACHE["name"]
    chosen = None
    try:
        flash = []
        for model in client.models.list():
            actions = getattr(model, "supported_actions", None) or []
            name = (getattr(model, "name", "") or "").replace("models/", "")
            if "generateContent" in actions and "flash" in name.lower() \
                    and "image" not in name.lower() and "tts" not in name.lower() \
                    and "live" not in name.lower():
                flash.append(name)
        if flash:
            # Prefer stable names: deprioritise preview/exp/lite, then shorter names.
            flash.sort(key=lambda n: ("preview" in n or "exp" in n, "lite" in n, len(n)))
            chosen = flash[0]
            logger.info("Gemini: selected model %s from models.list()", chosen)
    except Exception as error:
        logger.warning("Gemini: models.list() failed (%s); using fallbacks", error)
    _MODEL_CACHE["name"] = chosen
    return chosen


def _generate(prompt, api_key, temperature=0.2, max_tokens=4096, accept=None):
    """One answer from Gemini, trying the available Flash models in order.
    `accept(text)` turns the reply into the result (None = try the next model).
    Returns the result, or None on no key / any failure. Never raises."""
    if not api_key:
        return None
    accept = accept or (lambda t: t.strip() or None)
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key,
                              http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT_S * 1000))
        candidates = []
        selected = _select_flash_model(client)
        if selected:
            candidates.append(selected)
        candidates += [n for n in _FALLBACK_MODELS if n not in candidates]
        config = types.GenerateContentConfig(temperature=temperature,
                                             max_output_tokens=max_tokens)
        for name in candidates:
            try:
                response = client.models.generate_content(model=name, contents=prompt,
                                                          config=config)
                result = accept(getattr(response, "text", "") or "")
                if result:
                    return result
            except Exception as error:
                logger.warning("Gemini: model %s failed (%s)", name, error)
        return None
    except Exception as error:
        logger.warning("Gemini: call failed (%s)", error)
        return None


def explain_verdict(structured_data, api_key, depth="quick"):
    """
    Turn the already-computed `structured_data` dict into a plain-language
    explanation via Gemini. `depth` is "quick" (one tight paragraph) or "deep"
    (labeled sections). Returns the text, or None.

    Returns None (no exception, no network) when:
      - api_key is falsy (None/empty),
      - google-genai isn't importable,
      - listing + all fallback models fail,
      - the call errors or times out,
      - Gemini returns empty/whitespace.
    """
    if not api_key:
        return None  # optional feature off — skip silently, no import, no network
    try:
        prompt = _build_prompt(structured_data, depth)
    except Exception as error:
        logger.warning("Gemini: explain_verdict failed (%s)", error)
        return None
    return _generate(prompt, api_key, temperature=0.2,
                     max_tokens=_TOKENS_BY_DEPTH.get(depth, _TOKENS_BY_DEPTH["quick"]))



# --- News rating for the buy score (mockup item 12: "AI על החדשות", 20%) ---

def _news_prompt(symbol, name, headlines):
    lines = "\n".join(f"- {h.get('title','')} ({h.get('source','')})" for h in headlines)
    return (
        "You rate recent news for ONE stock for a private investor.\n"
        f"Stock: {name or symbol} ({symbol}).\n"
        "Headlines (title, source):\n" + lines + "\n\n"
        "Judge ONLY from these headlines how they likely affect the stock over the "
        "next months. Be calibrated: ordinary news is usually within -40..40; use "
        "beyond +-70 only for truly company-changing events, and 0 for mixed or "
        "irrelevant headlines. Answer with JSON only, no markdown:\n"
        '{"score": <integer -100 (very negative) .. 100 (very positive), 0 = neutral>, '
        '"reason": "<one short sentence in Hebrew explaining why>"}'
    )


def parse_news_rating(text):
    """Gemini's reply -> (score int -100..100, reason str) or None. Tolerates
    ```json fences and extra text around the JSON object."""
    import json as _json
    import re as _re
    if not text:
        return None
    m = _re.search(r"\{.*\}", text, _re.S)
    if not m:
        return None
    try:
        obj = _json.loads(m.group(0))
        score = int(round(float(obj.get("score"))))
    except (ValueError, TypeError, AttributeError):
        return None
    reason = str(obj.get("reason") or "").strip()
    return max(-100, min(100, score)), reason


def rate_news(symbol, name, headlines, api_key):
    """(score, reason_he) from Gemini for these headlines, or None (no key, no
    headlines, or any failure). One small call; never raises."""
    if not api_key or not headlines:
        return None
    return _generate(_news_prompt(symbol, name, headlines), api_key,
                     temperature=0.1, max_tokens=2048, accept=parse_news_rating)



# --- Shared: one JSON answer from Gemini (used by the features below) -------

def load_api_key():
    """GEMINI_API_KEY from the environment, else .streamlit/secrets.toml
    (for scripts like daily_job.py that run outside Streamlit). None if unset."""
    import os
    import tomllib
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".streamlit", "secrets.toml")
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh).get("GEMINI_API_KEY") or None
    except (OSError, ValueError):
        return None


def parse_json_object(text):
    """The first {...} object in a reply (tolerates ```json fences), or None."""
    import json as _json
    import re as _re
    if not text:
        return None
    m = _re.search(r"\{.*\}", text, _re.S)
    if not m:
        return None
    try:
        obj = _json.loads(m.group(0))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _ask_json(prompt, api_key, max_tokens=4096):
    """Ask once (trying the available Flash models) -> dict or None. Never raises."""
    return _generate(prompt, api_key, temperature=0.2, max_tokens=max_tokens,
                     accept=parse_json_object)


# --- Item 14: portfolio overview (words only; numbers come from the code) ---

def portfolio_overview(facts, api_key):
    """{"strengths": [..], "weaknesses": [..], "ideas": [..]} in Hebrew, or None."""
    import json as _json
    prompt = (
        "You review a private investor's stock portfolio. ALL numbers below were "
        "computed by code; do not invent other numbers.\n"
        + _json.dumps(facts, ensure_ascii=False) + "\n\n"
        "Write in simple Hebrew, short sentences, for a beginner. Return JSON only:\n"
        '{"strengths": [2 items], "weaknesses": [2-3 items], '
        '"ideas": [2-3 ideas to CONSIDER, e.g. diversification or a max weight per '
        'stock; never tell them to buy or sell a specific stock]}'
    )
    obj = _ask_json(prompt, api_key)
    if not obj:
        return None
    out = {k: [str(x).strip() for x in (obj.get(k) or []) if str(x).strip()][:3]
           for k in ("strengths", "weaknesses", "ideas")}
    return out if any(out.values()) else None


# --- Item 15: "AI recommends" from the nightly scan -------------------------

def pick_recommendations(candidates, api_key):
    """Raw {"short": [{symbol, why}], "long": [...]} chosen ONLY from the given
    candidates, or None. Validate with ai_insights.clean_picks()."""
    import json as _json
    prompt = (
        "From tonight's scan of ~611 stocks, code already kept the strongest "
        "candidates. Scores are 0..100 (50 = neutral); analyst_upside_pct is the "
        "Wall Street mean target vs today's price.\n"
        "SHORT term (weeks to months) candidates:\n"
        + _json.dumps(candidates.get("short", []), ensure_ascii=False) + "\n"
        "LONG term (a year and more) candidates:\n"
        + _json.dumps(candidates.get("long", []), ensure_ascii=False) + "\n\n"
        "Choose 2 or 3 per list, ONLY symbols from that list, preferring a mix of "
        "themes and reasonable risk. For each, one short Hebrew sentence why it is "
        "interesting to look at (ideas to check, not advice). Return JSON only:\n"
        '{"short": [{"symbol": "...", "why": "..."}], "long": [{"symbol": "...", "why": "..."}]}'
    )
    return _ask_json(prompt, api_key)
