"""
gemini_helper.py — OPTIONAL plain-language explanation of the verdict.

Gemini ONLY rephrases the already-computed structured data into a short, neutral
summary. It NEVER computes or invents numbers. The whole feature is optional and
crash-proof: with no API key, or on ANY error/timeout, explain_verdict() returns
None and the app shows exactly what it does today — no error, no empty box.

Design notes:
- No Streamlit here, so the data layer stays importable/testable on its own. The
  API key is always passed in as a string argument (app.py reads st.secrets).
- google-generativeai is imported LAZILY, inside explain_verdict, and only after
  we've confirmed there's a key. So importing this module never fails even if the
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
# against max_output_tokens, and this SDK (google-generativeai 0.8.x) can't set a
# thinking budget (thinking_config is rejected). With a small ceiling the budget
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


def _select_flash_model(genai):
    """Ask the API which models this key can use; pick a current Flash model that
    supports generateContent. Returns a model name, or None if none/availble."""
    try:
        flash = []
        for model in genai.list_models():
            methods = getattr(model, "supported_generation_methods", []) or []
            name = getattr(model, "name", "") or ""
            if "generateContent" in methods and "flash" in name.lower():
                flash.append(name)
        if flash:
            # Prefer stable names: deprioritise preview/exp, then shorter names.
            flash.sort(key=lambda n: ("preview" in n or "exp" in n, len(n)))
            chosen = flash[0]
            logger.info("Gemini: selected model %s from list_models()", chosen)
            return chosen
    except Exception as error:
        logger.warning("Gemini: list_models() failed (%s); using fallbacks", error)
    return None


def explain_verdict(structured_data, api_key, depth="quick"):
    """
    Turn the already-computed `structured_data` dict into a plain-language
    explanation via Gemini. `depth` is "quick" (one tight paragraph) or "deep"
    (labeled sections). Returns the text, or None.

    Returns None (no exception, no network) when:
      - api_key is falsy (None/empty),
      - google-generativeai isn't importable,
      - listing + all fallback models fail,
      - the call errors or times out,
      - Gemini returns empty/whitespace.
    """
    if not api_key:
        return None  # optional feature off — skip silently, no import, no network

    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        prompt = _build_prompt(structured_data, depth)
        max_tokens = _TOKENS_BY_DEPTH.get(depth, _TOKENS_BY_DEPTH["quick"])

        # Try the model the API recommends first, then the fallbacks in order.
        candidates = []
        selected = _select_flash_model(genai)
        if selected:
            candidates.append(selected)
        for name in _FALLBACK_MODELS:
            if name not in candidates:
                candidates.append(name)

        generation_config = {"temperature": 0.2, "max_output_tokens": max_tokens}
        for name in candidates:
            try:
                model = genai.GenerativeModel(
                    name, generation_config=generation_config)
                response = model.generate_content(
                    prompt, request_options={"timeout": _REQUEST_TIMEOUT_S})
                text = (getattr(response, "text", "") or "").strip()
                if text:
                    logger.info("Gemini: %s explanation generated with %s",
                                depth, name)
                    return text
            except Exception as error:
                logger.warning("Gemini: model %s failed (%s)", name, error)
                continue
        return None
    except Exception as error:
        # ANY failure -> log quietly and behave as if the feature is off.
        logger.warning("Gemini: explain_verdict failed (%s)", error)
        return None
