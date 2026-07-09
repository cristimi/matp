"""
LLM pricing table for cost estimation.
All prices are USD per 1,000,000 tokens (input / output).
Update this table when provider pricing changes.
R3: this module does NO provider calls — local arithmetic only.
"""

# (provider_lower, model_lower) -> (input_usd_per_1m, output_usd_per_1m)
_PRICING: dict[tuple[str, str], tuple[float, float]] = {
    # Google Gemini
    ('google', 'gemini-2.5-flash'):              (0.15,   0.60),
    ('google', 'gemini-2.5-flash-preview'):      (0.15,   0.60),
    ('google', 'gemini-2.0-flash'):              (0.075,  0.30),
    ('google', 'gemini-2.0-flash-lite'):         (0.075,  0.30),
    ('google', 'gemini-1.5-flash'):              (0.075,  0.30),
    ('google', 'gemini-1.5-flash-8b'):           (0.0375, 0.15),
    ('google', 'gemini-1.5-pro'):                (1.25,   5.00),
    ('google', 'gemini-2.5-pro'):                (1.25,  10.00),
    # Anthropic Claude
    ('anthropic', 'claude-3-5-haiku-20241022'):  (0.80,   4.00),
    ('anthropic', 'claude-3-5-sonnet-20241022'): (3.00,  15.00),
    ('anthropic', 'claude-3-7-sonnet-20250219'): (3.00,  15.00),
    ('anthropic', 'claude-opus-4-5'):            (15.00, 75.00),
    # OpenAI
    ('openai', 'gpt-4o-mini'):                   (0.15,   0.60),
    ('openai', 'gpt-4o'):                        (2.50,  10.00),
    ('openai', 'gpt-4-turbo'):                   (10.00, 30.00),
    ('openai', 'o1-mini'):                       (1.10,   4.40),
    ('openai', 'o1'):                            (15.00, 60.00),
    # Groq (hosted open-weight models — verify against groq.com/pricing, changes often)
    ('groq', 'llama-3.1-8b-instant'):            (0.05,   0.08),
    ('groq', 'llama-3.3-70b-versatile'):         (0.59,   0.79),
    ('groq', 'gemma2-9b-it'):                    (0.20,   0.20),
    ('groq', 'deepseek-r1-distill-llama-70b'):   (0.75,   0.99),
}

_FALLBACK: tuple[float, float] = (0.075, 0.30)   # Gemini 2.0 Flash

# Per-provider fallback for an unrecognized model of a known provider —
# closer to reality than defaulting every unknown model to Gemini pricing.
_PROVIDER_FALLBACK: dict[str, tuple[float, float]] = {
    'groq': (0.05, 0.08),   # llama-3.1-8b-instant — cheapest Groq default
}


def get_pricing(provider: str, model: str) -> tuple[float, float]:
    """
    Return (input_usd_per_1m, output_usd_per_1m) for the given provider/model.
    Falls back to a per-provider default, then _FALLBACK, if not found.
    Matching is case-insensitive; partial prefix match is attempted on the model name.
    """
    p = provider.lower().strip()
    m = model.lower().strip()

    # Exact match first
    if (p, m) in _PRICING:
        return _PRICING[(p, m)]

    # Prefix match: e.g. 'gemini-2.0-flash-001' → 'gemini-2.0-flash'
    for (tp, tm), pricing in _PRICING.items():
        if tp == p and (m.startswith(tm) or tm.startswith(m)):
            return pricing

    return _PROVIDER_FALLBACK.get(p, _FALLBACK)
