"""
SymbolFactory: constructs exchange-specific symbol strings
from a normalized (base_asset, quote_asset) pair.
"""

EXCHANGE_FORMATTERS = {
    "blofin":      lambda base, quote: f"{base}-{quote}",       # BTC-USDT
    "hyperliquid": lambda base, quote: base,                     # BTC
}

class SymbolFactory:
    @staticmethod
    def format(base: str, quote: str, exchange: str) -> str:
        formatter = EXCHANGE_FORMATTERS.get(exchange.lower())
        if not formatter:
            raise ValueError(f"Unknown exchange: {exchange}")
        return formatter(base, quote)

    @staticmethod
    def parse_blofin(symbol: str) -> tuple[str, str]:
        """Parse 'BTC-USDT' → ('BTC', 'USDT'). Used during migration."""
        parts = symbol.split("-")
        if len(parts) != 2:
            raise ValueError(f"Cannot parse Blofin symbol: {symbol}")
        return parts[0], parts[1]

    @staticmethod
    def split(symbol: str) -> tuple[str, str]:
        """Split 'BTC/USDT', 'BTC-USDT' or 'BTCUSDT' into (Base, Quote)."""
        if "/" in symbol:
            parts = symbol.split("/")
        elif "-" in symbol:
            parts = symbol.split("-")
        else:
            # Common stablecoin quotes
            for quote in ["USDT", "USDC", "USD", "DAI"]:
                if symbol.endswith(quote):
                    return symbol[:-len(quote)], quote
            raise ValueError(f"Cannot split symbol: {symbol}")
        
        if len(parts) != 2:
             raise ValueError(f"Cannot split symbol: {symbol}")
        return parts[0], parts[1]
