"""
Symbol Coupling validator for the MATP Order Listener.

Resolves incoming base_asset + quote_asset to the strategy's configured
execution symbol, applying the two coupling tolerance flags.

Resolution modes (in priority order):
  1. Strict (default): constructed symbol must exactly match execution symbol.
  2. Quote variants (allow_quote_variants=True): USD, USDC, USDT, PERP are
     interchangeable as quote currencies.
  3. Cross-charting (allow_cross_charting=True): only base asset is matched;
     quote currency is ignored entirely.

Safety rule: if either loose coupling mode (2 or 3) resolves a mismatch,
price, tp_price, and sl_price MUST be stripped (set to None) before the
OrderRequest is dispatched to the executor. This prevents index chart prices
or cross-currency prices from reaching the exchange.
"""
from dataclasses import dataclass
from typing import Optional


# Quote currencies treated as interchangeable by allow_quote_variants
QUOTE_VARIANTS: frozenset[str] = frozenset({"USD", "USDC", "USDT", "PERP"})


class SymbolMismatchError(Exception):
    """
    Raised when the incoming assets cannot be resolved to the strategy's
    execution symbol under any enabled coupling mode.
    """
    def __init__(self, incoming: str, execution: str, message: str):
        self.incoming = incoming
        self.execution = execution
        super().__init__(message)


@dataclass
class ResolvedSymbol:
    """
    Result of a successful symbol resolution.

    Attributes:
        execution_symbol: The canonical symbol to use in the OrderRequest.
                          Always equals strategies.symbol — never the raw
                          incoming constructed symbol.
        price_stripped:   True if price, tp_price, sl_price must be set to
                          None before dispatching to the executor.
        coupling_used:    Which coupling mode resolved the symbol.
                          None = strict match (no coupling needed).
                          "quote_variants" = allow_quote_variants flag used.
                          "cross_charting" = allow_cross_charting flag used.
    """
    execution_symbol: str
    price_stripped:   bool
    coupling_used:    Optional[str]


def resolve_symbol(
    base_asset:           str,
    quote_asset:          str,
    execution_symbol:     str,
    allow_quote_variants: bool,
    allow_cross_charting: bool,
) -> ResolvedSymbol:
    """
    Resolve incoming base_asset + quote_asset to the strategy's execution symbol.

    Args:
        base_asset:           e.g. "BTC"
        quote_asset:          e.g. "USDC", "USD", "EUR"
        execution_symbol:     e.g. "BTC-USDT" (from strategies.symbol)
        allow_quote_variants: If True, treat USD/USDC/USDT/PERP as equivalent.
        allow_cross_charting: If True, match base asset only; ignore quote.

    Returns:
        ResolvedSymbol with execution_symbol, price_stripped, coupling_used.

    Raises:
        SymbolMismatchError: if no enabled coupling mode can resolve the mismatch.
        ValueError: if execution_symbol is not in "BASE-QUOTE" format.
    """
    base  = base_asset.strip().upper()
    quote = quote_asset.strip().upper()
    incoming_constructed = f"{base}-{quote}"

    # Parse execution symbol
    if "-" not in execution_symbol:
        raise ValueError(
            f"execution_symbol '{execution_symbol}' is not in BASE-QUOTE format. "
            "Expected e.g. 'BTC-USDT'."
        )
    exec_base, exec_quote = execution_symbol.upper().split("-", 1)

    # ── Mode 1: Strict match ──────────────────────────────────────────
    if incoming_constructed == execution_symbol.upper():
        return ResolvedSymbol(
            execution_symbol=execution_symbol,
            price_stripped=False,
            coupling_used=None,
        )

    # ── Mode 2: Quote variants ────────────────────────────────────────
    if allow_quote_variants:
        if (
            base == exec_base
            and quote in QUOTE_VARIANTS
            and exec_quote in QUOTE_VARIANTS
        ):
            return ResolvedSymbol(
                execution_symbol=execution_symbol,
                price_stripped=(quote != exec_quote),  # strip only if quotes differ
                coupling_used="quote_variants" if quote != exec_quote else None,
            )

    # ── Mode 3: Cross-charting ────────────────────────────────────────
    if allow_cross_charting:
        if base == exec_base:
            return ResolvedSymbol(
                execution_symbol=execution_symbol,
                price_stripped=True,   # always strip — quote is unknown/different
                coupling_used="cross_charting",
            )

    # ── No mode resolved the mismatch ────────────────────────────────
    enabled_modes = []
    if allow_quote_variants:
        enabled_modes.append("quote_variants")
    if allow_cross_charting:
        enabled_modes.append("cross_charting")

    modes_str = (
        f" (enabled modes: {', '.join(enabled_modes)})"
        if enabled_modes
        else " (no coupling flags enabled)"
    )

    raise SymbolMismatchError(
        incoming=incoming_constructed,
        execution=execution_symbol,
        message=(
            f"Symbol mismatch: incoming '{incoming_constructed}' cannot be "
            f"resolved to execution symbol '{execution_symbol}'{modes_str}."
        ),
    )
