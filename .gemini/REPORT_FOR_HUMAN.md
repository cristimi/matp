# HL leverage + margin overrun — diagnostic findings
_Generated 2026-06-16. Incident: HLTest open_long/open_short orders placed 2026-06-16 ~17:34–17:40 UTC._

---

## Records

### 3a — Strategy config (HLTest)

```
id           | name   | platform | account_id      | enabled | default_leverage | max_leverage | margin_mode | capital_allocation | margin_per_trade | max_drawdown_pct
hltest-76b3  | HLTest | auto     | Hyperliquidtest | t       |               20 |           50 | isolated    |                 90 |                5 |               50
```

**Key observation:** `default_leverage = 20`, NOT 5. The strategy was previously used with 20x
(June 15 webhook explicitly sent `"leverage": "20"`). That 20x became the HL per-coin persistent setting.

---

### 3b — Order rows (most recent 5, highlight: stored leverage & size)

```
received_at                    | signal     | size   | leverage | status | actual_fill_price | raw_webhook leverage field
2026-06-16 17:40:16 (incident) | open_long  | 0.05   |        5 | filled |          65804.4  | "leverage": "5"
2026-06-16 17:36:31 (incident) | open_long  | 0.05   |        5 | filled |          65769.0  | "leverage": "5"
2026-06-16 17:34:52 (incident) | open_short | 0.05   |        5 | filled |          65697.7  | "leverage": "5"
2026-06-15 17:24:15 (root)     | open_short | 0.01   |       20 | filled |          66880.2  | "leverage": "20" ← set HL persistent 20x
2026-06-13 15:35:19            | open_long  | 0.0005 |       20 | filled |          64486.0  | (no leverage field → default_leverage=20)
```

**Stored leverage for incident orders: 5** — the listener correctly parsed and stored 5x from the webhook.
**Exchange leverage used: 20** — HL persistent coin setting, never updated by the adapter (see §Code Evidence).

---

### 3c — Signal log (raw webhook bodies)

```
received_at                    | outcome | raw_body (abridged)
2026-06-16 17:40:16 (incident) | filled  | {..., "leverage": "5",  "size": "0.05", "indicator_price": null, "price": null}
2026-06-16 17:36:31 (incident) | filled  | {..., "leverage": "5",  "size": "0.05", "indicator_price": null, "price": null}
2026-06-16 17:34:52 (incident) | filled  | {..., "leverage": "5",  "size": "0.05", "indicator_price": null, "price": null}
2026-06-15 17:24:15 (root)     | filled  | {..., "leverage": "20", "size": "0.01", "indicator_price": null, "price": null}
2026-06-13 15:35:19            | filled  | {no leverage field,      "size": "0.0005", "indicator_price": "107000"}
```

**Critical detail:** All incident webhooks have `indicator_price: null` AND `price: null`.
This is the exact condition that bypasses the margin clamp (see §5 Q5).

---

### 3d — Strategy positions (most recent 5)

```
opened_at                      | symbol   | side  | size   | leverage | margin_mode | status | entry_price
2026-06-16 17:40:19 (current)  | BTC-USDT | long  | 0.05   |        5 | isolated    | open   | 65804.4
2026-06-16 17:36:34 (incident) | BTC-USDT | long  | 0.05   |        5 | isolated    | closed | 65769.0
2026-06-16 17:34:54 (incident) | BTC-USDT | short | 0.05   |        5 | isolated    | closed | 65697.7
2026-06-15 17:24:19 (root)     | BTC-USDT | short | 0.01   |       20 | isolated    | closed | 66880.2
2026-06-13 15:35:23            | BTC-USDT | long  | 0.0005 |       20 | isolated    | closed | 64486.0
```

Positions store MATP's computed leverage (5), not what HL actually applied. There is no post-fill
mismatch check between stored leverage and position leverage returned by `clearinghouseState`.

---

## Code evidence

### WebhookPayload leverage field

`order-listener/app/models.py:51`
```python
leverage: Optional[int] = None
```
Field name is `leverage`, type `Optional[int]`. Pydantic coerces the string `"5"` to `int(5)`
successfully — confirmed by signal_log showing `"leverage": "5"` in raw_body while orders table
stores integer `5`.

---

### effective_leverage resolution

`order-listener/app/webhook_handler.py:516–521`
```python
effective_leverage = (
    int(payload.leverage)
    if payload.leverage is not None
    else int(strategy.get("default_leverage") or 1)
)
```

- Incident orders: `payload.leverage = 5` (not None) → `effective_leverage = 5`. **Correct.**
- June 15 order: `payload.leverage = 20` → `effective_leverage = 20`.
- June 13 order: `payload.leverage = None` (field absent) → `default_leverage = 20`.

The listener computed and stored the correct value for all cases. The leverage defect is downstream.

---

### Guard 3 outcome

`order-listener/app/webhook_handler.py:604–613`
```python
max_lev = int(strategy.get("max_leverage", 10) or 10)
if effective_leverage > max_lev:
    raise HTTPException(status_code=422, ...)
```

`max_leverage = 50` for HLTest. `effective_leverage = 5`. **Guard 3 did not trip.** 5 ≤ 50.
This guard was never a factor in the incident.

---

### Margin clamp: fired? NO — reason: _ref_price = 0

`order-listener/app/webhook_handler.py:615–634`
```python
if payload.signal in ("open_long", "open_short"):
    _margin_per_trade = float(strategy.get("margin_per_trade") or 5.0)
    _ref_price        = float(payload.indicator_price or payload.price or 0)
    if _ref_price > 0:                                   # ← gate: entire clamp inside here
        _margin_qty = round((_margin_per_trade * effective_leverage) / _ref_price, 8)
        if float(payload.size) > _margin_qty:
            payload.size = Decimal(str(_margin_qty))
            ...
            logger.info(f"Strategy {strategy_id} margin clamp: ...")
```

For all incident orders: `indicator_price = None`, `price = None` → `_ref_price = 0.0`.
**The `if _ref_price > 0:` gate is False → the entire clamp block is skipped.**

Confirmed by absence of any "margin clamp:" log line for HLTest orders in the 24h window.
The only relevant listener log lines were:
```
2026-06-16 17:34:52 [WARNING] strategy=hltest-76b3: no reference price for guaranteed SL
2026-06-16 17:36:31 [WARNING] strategy=hltest-76b3: no reference price for guaranteed SL
2026-06-16 17:40:16 [WARNING] strategy=hltest-76b3: no reference price for guaranteed SL
```

**Four input values at the clamp site (incident orders):**
| Variable | Value | Source |
|---|---|---|
| `_margin_per_trade` | 5.0 | strategy config |
| `effective_leverage` | 5 | webhook `"leverage":"5"` |
| `_ref_price` | **0** | no `indicator_price`, no `price` in webhook |
| `payload.size` | 0.05 BTC | TV-supplied, unmodified |

If TV had included a price (~$65,800):
`_margin_qty = (5 × 5) / 65800 = 0.000380 BTC`. TV sent 0.05 — **131× over cap**.
The clamp would have fired and reduced size to 0.000380 BTC.

---

### hyperliquid.py set-leverage: **ABSENT**

`order-executor/app/adapters/hyperliquid.py:225–438` (`_place_order` in full) was read completely.

There is **no `updateLeverage` action, no `/exchange` POST for leverage, and no reference to
`order.leverage`** anywhere in `_place_order` or anywhere else in `hyperliquid.py`.
The field `order.leverage` (carried in `OrderRequest`) is available to the adapter but **never read**.

The only `/exchange` POST the HL adapter sends is the order itself (action type `"order"`).

**Confirmed by executor logs** (30h window — smoking gun):
```
# Blofin orders — explicit set-leverage logged BEFORE each /exchange POST:
2026-06-16 15:39:40  POST https://demo-trading-api.blofin.com/api/v1/account/set-leverage  200 OK
2026-06-16 15:39:40  BlofinAdapter: leverage set to 10x for BTC-USDT (isolated)
2026-06-16 16:39:59  POST https://demo-trading-api.blofin.com/api/v1/account/set-leverage  200 OK
2026-06-16 16:39:59  BlofinAdapter: leverage set to 10x for BTC-USDT (isolated)

# Hyperliquid orders — bare /exchange POST only, zero set-leverage:
2026-06-15 17:24:18  POST https://api.hyperliquid-testnet.xyz/exchange  200 OK  ← sets HL BTC to 20x
2026-06-16 17:34:54  POST https://api.hyperliquid-testnet.xyz/exchange  200 OK  ← HL still 20x → 20x used
2026-06-16 17:36:34  POST https://api.hyperliquid-testnet.xyz/exchange  200 OK  ← same
2026-06-16 17:40:19  POST https://api.hyperliquid-testnet.xyz/exchange  200 OK  ← same
```

---

### blofin.py _set_leverage

`order-executor/app/adapters/blofin.py:149–169`
```python
async def _set_leverage(self, inst_id: str, leverage: int, margin_mode: str) -> None:
    """Set leverage for an instrument before placing an order."""
    path = "/api/v1/account/set-leverage"
    body_data = {"instId": inst_id, "leverage": str(leverage),
                 "marginMode": margin_mode, "positionSide": "net"}
    ...
    logger.info(f"BlofinAdapter: leverage set to {leverage}x for {inst_id} ({margin_mode})")
```

Called unconditionally at `blofin.py:182` inside `submit_order`:
```python
await self._set_leverage(order.symbol, leverage, margin_mode)
```

**HL has no equivalent method and no equivalent call.**

---

### hyperliquid.py size_wire derivation

`order-executor/app/adapters/hyperliquid.py:279`
```python
size_wire = self._float_to_wire(float(order.size))
```

Raw `order.size` (= TV-supplied 0.05 BTC) forwarded directly. Zero margin or leverage computation
in the adapter. Whatever size MATP sends, HL receives verbatim.

---

## Answers to §5 (1–7)

**1. Webhook leverage parsing / stored leverage:**
- Exact field name in `WebhookPayload`: `leverage` (`Optional[int] = None`, `models.py:51`).
- Stored `leverage` for incident orders (3b): **5**.
- The listener parsed and stored 5x correctly. The leverage mismatch is purely in the adapter.

**2. Guard 3:**
- `max_leverage` for HLTest = **50** (3a).
- `effective_leverage` = 5. 5 ≤ 50 → Guard 3 **did not trip** and was not relevant to this incident.

**3. Adapter leverage (Hypothesis A): CONFIRMED — updateLeverage is ABSENT from hyperliquid.py.**
- `_place_order` (lines 225–438) never reads `order.leverage` and never sends an `updateLeverage`
  action to HL. Leverage is a persistent per-coin account setting on Hyperliquid.
- The June 15 webhook sent `leverage=20` → HL executed at 20x → BTC coin leverage locked at 20x.
- June 16 webhooks sent `leverage=5` → MATP stored 5x → adapter ignored it → HL still at 20x.
- Blofin sends `set-leverage` before every order (lines 149–182); HL has no equivalent.

**4. Size to exchange:**
- `hyperliquid.py:279`: `size_wire = self._float_to_wire(float(order.size))`
- Raw `order.size` is passed directly. No margin or leverage derivation in the adapter.

**5. Margin clamp (Hypothesis B): NOT fired. Reason: _ref_price = 0 (no price in webhook).**
- All incident webhooks had `indicator_price = None`, `price = None` (confirmed 3c).
- `_ref_price = float(None or None or 0) = 0.0` → `if _ref_price > 0:` gate is False → clamp skipped.
- TV-supplied `size = 0.05 BTC` passed through unmodified.
- Four values: `_margin_per_trade=5.0`, `effective_leverage=5`, `_ref_price=0`, `payload.size=0.05`.
- No "margin clamp:" log line emitted for HLTest in 24h window.

**6. Margin reconstruction:**

HL used 20x (persistent, set by June 15 order). Size = 0.05 BTC (TV, unmodified). BTC ~$65,800.

```
Notional  = 0.05 BTC × $65,800        = $3,290.00
Margin @  = $3,290 ÷ 20 (HL's actual) = $164.50   ≈ $160  ✓

Individual fills:
  open_short 17:34  0.05 × $65,697.7 ÷ 20 = $164.24
  open_long  17:36  0.05 × $65,769.0 ÷ 20 = $164.42
  open_long  17:40  0.05 × $65,804.4 ÷ 20 = $164.51

Counterfactual — if leverage had been 5x (updateLeverage had worked):
  Margin    = $3,290 ÷ 5 = $658.00   (still 131× over $5 limit — clamp failure still dominates)

Correct outcome — leverage 5x AND clamp fired (price present):
  Target size = ($5 × 5) / $65,800 = 0.000380 BTC
  Margin      = 0.000380 × $65,800 ÷ 5 = $5.00  ✓
```

**The ~$160 figure requires both defects simultaneously:**
- 20x HL leverage (Hypothesis A) → margin = $164 instead of $658
- 0.05 BTC size unclamped (Hypothesis B) → any leverage × 0.05 BTC is far over $5 limit

At 5x leverage with the correct clamped size, margin = $5 exactly.

**7. Account/coin leverage state:**
- No `clearinghouseState` asset-context leverage query was made during order placement (logs confirm
  only bare `/exchange` POSTs for HL orders, no pre-flight info query for leverage).
- The causal chain is derivable from the order table: June 15 order used `leverage=20` in the webhook,
  executed at 20x on HL, left BTC coin at 20x persistently. All June 16 orders ran against that
  stale setting.
- MATP reads HL position leverage in `get_open_positions` (`hyperliquid.py:155`:
  `p.get("leverage", {}).get("value", 1)`), but this happens asynchronously for the reconciler,
  not during order placement, and no mismatch alert exists.

---

## Margin arithmetic

```
Incident parameters:
  size           = 0.05 BTC  (TV-supplied, margin clamp bypassed — no price in webhook)
  fill price     ≈ $65,800   (average of three fills: $65,697.7, $65,769.0, $65,804.4)
  HL leverage    = 20x       (persistent account setting; HL adapter never called updateLeverage)

Margin used     = 0.05 × 65,800 / 20 = $164.50  ≈ $160  ✓

Which inputs produce it: size=0.05 (TV, unmodified) + leverage=20 (HL stale setting).
```

---

## One-paragraph conclusion

**Two independent defects compounded to produce all three symptoms.** Defect A (Hypothesis A, **confirmed**): `HyperliquidAdapter._place_order` (`hyperliquid.py:225–438`) never reads `order.leverage` and never sends a Hyperliquid `updateLeverage` action. On Hyperliquid, leverage is a persistent per-coin account setting. The June 15 webhook sent `leverage=20`, which was executed at 20x and left BTC leverage locked at 20x on the account. When the June 16 webhooks arrived with `leverage=5`, MATP correctly computed, stored (`orders.leverage=5`), and forwarded `effective_leverage=5` — but the HL adapter ignored the field entirely, submitted the order bare, and HL used its stale 20x setting. In contrast, BlofinAdapter calls `_set_leverage` before every order (`blofin.py:149–182`), as confirmed in executor logs showing explicit `set-leverage` POSTs for Blofin and zero such calls for Hyperliquid. Defect B (Hypothesis B, **confirmed**): The margin-per-trade clamp in `webhook_handler.py:615–634` is gated on `_ref_price > 0`; all incident webhooks omitted `indicator_price` and `price`, making `_ref_price=0.0`, which caused the clamp to be skipped entirely. TV supplied `size=0.05 BTC` — 131× larger than the `0.000380 BTC` the clamp would have enforced (`$5 × 5 / $65,800`). The ~$160 margin figure is the product of both defects: `0.05 BTC × $65,800 / 20 = $164.50`. Defect A (stale 20x) caused the leverage symptom directly; Defect B (bypassed clamp) caused the size/margin symptom independently; their combination produced the ~$160 figure. **Confidence: very high for both hypotheses** — Defect A is absent code confirmed against a working reference (Blofin) and confirmed by executor logs; Defect B is confirmed by `_ref_price=0` code path, absent price fields in raw signal_log bodies, and no "margin clamp:" log line. **Remaining unknown:** whether the HL testnet account's BTC leverage is still set to 20x at time of reading — a `clearinghouseState` query for the account would confirm, but was not executed (read-only investigation scope).
