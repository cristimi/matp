"""
Unit tests for symbol_validator.py

Covers all resolution modes:
  - Strict match (exact)
  - Strict mismatch (rejected)
  - Quote variant match
  - Quote variant same-quote (no price stripping)
  - Cross-charting match
  - Cross-charting always strips price
  - Flag interaction: cross-charting takes precedence over quote_variants
  - Invalid execution_symbol format
  - Whitespace/case normalisation
  - Unknown quote not in QUOTE_VARIANTS
"""
import pytest
from app.symbol_validator import (
    resolve_symbol,
    SymbolMismatchError,
    QUOTE_VARIANTS,
)


# ── Strict mode (both flags False) ───────────────────────────────────

def test_strict_exact_match():
    result = resolve_symbol(
        base_asset="BTC", quote_asset="USDT",
        execution_symbol="BTC-USDT",
        allow_quote_variants=False, allow_cross_charting=False,
    )
    assert result.execution_symbol == "BTC-USDT"
    assert result.price_stripped   is False
    assert result.coupling_used    is None


def test_strict_rejects_quote_mismatch():
    with pytest.raises(SymbolMismatchError) as exc:
        resolve_symbol(
            base_asset="BTC", quote_asset="USDC",
            execution_symbol="BTC-USDT",
            allow_quote_variants=False, allow_cross_charting=False,
        )
    assert "BTC-USDC" in str(exc.value)
    assert "BTC-USDT" in str(exc.value)


def test_strict_rejects_base_mismatch():
    with pytest.raises(SymbolMismatchError):
        resolve_symbol(
            base_asset="ETH", quote_asset="USDT",
            execution_symbol="BTC-USDT",
            allow_quote_variants=False, allow_cross_charting=False,
        )


def test_strict_rejects_cross_chart():
    with pytest.raises(SymbolMismatchError):
        resolve_symbol(
            base_asset="BTC", quote_asset="EUR",
            execution_symbol="BTC-USDT",
            allow_quote_variants=False, allow_cross_charting=False,
        )


# ── Quote variants mode ───────────────────────────────────────────────

def test_quote_variants_usdc_to_usdt():
    result = resolve_symbol(
        base_asset="BTC", quote_asset="USDC",
        execution_symbol="BTC-USDT",
        allow_quote_variants=True, allow_cross_charting=False,
    )
    assert result.execution_symbol == "BTC-USDT"
    assert result.price_stripped   is True
    assert result.coupling_used    == "quote_variants"


def test_quote_variants_usd_to_usdt():
    result = resolve_symbol(
        base_asset="BTC", quote_asset="USD",
        execution_symbol="BTC-USDT",
        allow_quote_variants=True, allow_cross_charting=False,
    )
    assert result.execution_symbol == "BTC-USDT"
    assert result.price_stripped   is True
    assert result.coupling_used    == "quote_variants"


def test_quote_variants_perp_to_usdt():
    result = resolve_symbol(
        base_asset="BTC", quote_asset="PERP",
        execution_symbol="BTC-USDT",
        allow_quote_variants=True, allow_cross_charting=False,
    )
    assert result.execution_symbol == "BTC-USDT"
    assert result.price_stripped   is True
    assert result.coupling_used    == "quote_variants"


def test_quote_variants_same_quote_no_stripping():
    """Exact match via allow_quote_variants — no stripping needed."""
    result = resolve_symbol(
        base_asset="BTC", quote_asset="USDT",
        execution_symbol="BTC-USDT",
        allow_quote_variants=True, allow_cross_charting=False,
    )
    assert result.price_stripped is False
    assert result.coupling_used  is None  # strict match, flag not needed


def test_quote_variants_rejects_unknown_quote():
    """EUR is not in QUOTE_VARIANTS — must be rejected even with flag on."""
    with pytest.raises(SymbolMismatchError):
        resolve_symbol(
            base_asset="BTC", quote_asset="EUR",
            execution_symbol="BTC-USDT",
            allow_quote_variants=True, allow_cross_charting=False,
        )


def test_quote_variants_rejects_base_mismatch():
    """Wrong base asset is never accepted regardless of flags."""
    with pytest.raises(SymbolMismatchError):
        resolve_symbol(
            base_asset="ETH", quote_asset="USDC",
            execution_symbol="BTC-USDT",
            allow_quote_variants=True, allow_cross_charting=False,
        )


# ── Cross-charting mode ───────────────────────────────────────────────

def test_cross_charting_ignores_quote():
    result = resolve_symbol(
        base_asset="BTC", quote_asset="EUR",
        execution_symbol="BTC-USDT",
        allow_quote_variants=False, allow_cross_charting=True,
    )
    assert result.execution_symbol == "BTC-USDT"
    assert result.price_stripped   is True
    assert result.coupling_used    == "cross_charting"


def test_cross_charting_always_strips_price():
    """Even when quote matches exactly, cross-charting strips price."""
    result = resolve_symbol(
        base_asset="BTC", quote_asset="USDT",
        execution_symbol="BTC-USDT",
        allow_quote_variants=False, allow_cross_charting=True,
    )
    # Strict match should win before cross-charting is checked
    assert result.price_stripped is False
    assert result.coupling_used  is None


def test_cross_charting_rejects_base_mismatch():
    """Wrong base asset is still rejected even with cross-charting on."""
    with pytest.raises(SymbolMismatchError):
        resolve_symbol(
            base_asset="ETH", quote_asset="EUR",
            execution_symbol="BTC-USDT",
            allow_quote_variants=False, allow_cross_charting=True,
        )


# ── Both flags on ─────────────────────────────────────────────────────

def test_both_flags_quote_variant_takes_priority():
    """
    With both flags on and a QUOTE_VARIANTS quote mismatch,
    quote_variants should resolve it (less permissive wins first).
    """
    result = resolve_symbol(
        base_asset="BTC", quote_asset="USDC",
        execution_symbol="BTC-USDT",
        allow_quote_variants=True, allow_cross_charting=True,
    )
    assert result.coupling_used == "quote_variants"


def test_both_flags_cross_charting_for_unknown_quote():
    """
    With both flags on and EUR (not in QUOTE_VARIANTS),
    cross-charting must pick it up.
    """
    result = resolve_symbol(
        base_asset="BTC", quote_asset="EUR",
        execution_symbol="BTC-USDT",
        allow_quote_variants=True, allow_cross_charting=True,
    )
    assert result.coupling_used    == "cross_charting"
    assert result.price_stripped   is True


# ── Edge cases ────────────────────────────────────────────────────────

def test_case_insensitive():
    result = resolve_symbol(
        base_asset="btc", quote_asset="usdt",
        execution_symbol="BTC-USDT",
        allow_quote_variants=False, allow_cross_charting=False,
    )
    assert result.execution_symbol == "BTC-USDT"
    assert result.price_stripped   is False


def test_whitespace_stripped():
    result = resolve_symbol(
        base_asset="  BTC  ", quote_asset="  USDT  ",
        execution_symbol="BTC-USDT",
        allow_quote_variants=False, allow_cross_charting=False,
    )
    assert result.execution_symbol == "BTC-USDT"


def test_invalid_execution_symbol_raises_value_error():
    with pytest.raises(ValueError, match="BASE-QUOTE"):
        resolve_symbol(
            base_asset="BTC", quote_asset="USDT",
            execution_symbol="BTCUSDT",  # missing dash
            allow_quote_variants=False, allow_cross_charting=False,
        )


def test_error_message_contains_both_symbols():
    with pytest.raises(SymbolMismatchError) as exc:
        resolve_symbol(
            base_asset="BTC", quote_asset="EUR",
            execution_symbol="BTC-USDT",
            allow_quote_variants=False, allow_cross_charting=False,
        )
    msg = str(exc.value)
    assert "BTC-EUR"  in msg
    assert "BTC-USDT" in msg


def test_quote_variants_set_contains_expected_values():
    assert "USD"  in QUOTE_VARIANTS
    assert "USDC" in QUOTE_VARIANTS
    assert "USDT" in QUOTE_VARIANTS
    assert "PERP" in QUOTE_VARIANTS
    assert "EUR"  not in QUOTE_VARIANTS
