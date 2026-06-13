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
}

_FALLBACK: tuple[float, float] = (0.075, 0.30)   # Gemini 2.0 Flash


def get_pricing(provider: str, model: str) -> tuple[float, float]:
    """
    Return (input_usd_per_1m, output_usd_per_1m) for the given provider/model.
    Falls back to _FALLBACK if not found.
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

    return _FALLBACK
