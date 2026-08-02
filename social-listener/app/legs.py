"""What the channel is recorded as holding in one asset, as legs.

The listener used to model an asset as a single stance — FLAT, LONG or SHORT — with
LONG->SHORT expressed as a flip. That is a faithful model of a net-mode exchange
account and an unfaithful model of the trader, who holds a long and a short in the
same coin when he wants to. This module is the vocabulary for the second version:
an asset has two legs, each independently open or flat.

`Legs` is deliberately tiny and immutable-ish. It exists so that "which side are we
on" stops being a string comparison scattered through the state machine — with two
legs, `cur_state == "LONG"` has no single right answer, and every place that asked
it had to be found and made to say which leg it meant.
"""

LONG = "LONG"
SHORT = "SHORT"
SIDES = (LONG, SHORT)


def opposite(side: str) -> str:
    return SHORT if side == LONG else LONG


class Legs:
    """The open/flat state of both legs of one asset."""

    __slots__ = ("long", "short")

    def __init__(self, long: bool = False, short: bool = False):
        self.long = bool(long)
        self.short = bool(short)

    # ── construction ─────────────────────────────────────────────────────────

    @classmethod
    def from_sides(cls, sides) -> "Legs":
        """From any iterable of open side names."""
        s = {str(x).upper() for x in (sides or ())}
        return cls(long=LONG in s, short=SHORT in s)

    @classmethod
    def from_stance(cls, stance: str | None) -> "Legs":
        """From the OLD single-stance string. Kept for the backtest replay, which
        prices a linear one-position-at-a-time timeline and is meaningful only in
        net mode."""
        return cls(long=stance == LONG, short=stance == SHORT)

    # ── reading ──────────────────────────────────────────────────────────────

    def is_open(self, side: str) -> bool:
        return self.long if side == LONG else self.short

    @property
    def open_sides(self) -> list[str]:
        return [s for s in SIDES if self.is_open(s)]

    @property
    def count(self) -> int:
        return len(self.open_sides)

    @property
    def flat(self) -> bool:
        return self.count == 0

    def sole_open(self) -> str | None:
        """The one open leg, or None when zero or both are open.

        This is what lets a post that never names a side still be actionable: with
        one leg open there is nothing to disambiguate. With both open there is, and
        the caller must not guess.
        """
        opens = self.open_sides
        return opens[0] if len(opens) == 1 else None

    def with_side(self, side: str, is_open: bool) -> "Legs":
        nxt = Legs(self.long, self.short)
        if side == LONG:
            nxt.long = is_open
        else:
            nxt.short = is_open
        return nxt

    # ── display ──────────────────────────────────────────────────────────────

    def label(self) -> str:
        """Compact form for the audit row's from_state / to_state.

        'FLAT' | 'LONG' | 'SHORT' | 'LONG+SHORT'. The single-leg spellings are the
        SAME strings the old single-stance model wrote, so historical shadow rows and
        new ones read alike and the backtest's seed query keeps working.
        """
        opens = self.open_sides
        return "+".join(opens) if opens else "FLAT"

    def __eq__(self, other) -> bool:
        return (isinstance(other, Legs)
                and self.long == other.long and self.short == other.short)

    def __repr__(self) -> str:
        return f"Legs({self.label()})"
