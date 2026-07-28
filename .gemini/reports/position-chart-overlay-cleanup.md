# Position card: de-duplicate the chart overlay, move Risk/Reward into the details grid

Date: 2026-07-28

## What changed

**1. No more text drawn over the candles.**
`src/charts/adapters/lightweightCharts/riskRewardPrimitive.ts` drew three chips
beside the newest levels — `TP 72.378 +1.67%`, `SL 74.256 −0.88%`,
`73.610 R:R 1.91`. Every one of those prices already had a tag on the right-hand
price axis, placed by the `createPriceLine` calls in the adapter
(`index.ts:203-220`, `axisLabelVisible: true`). The chips said it twice and covered
the bars doing it.

The chips are gone. Everything else the primitive draws stays: the profit and loss
zones, the solid entry line on each rung, the dashed risers between rungs, and the
progress band. The price-axis tags are untouched, so SL / entry / TP are still
labelled — once.

Two now-dead constants went with them (`colors.label`, `colors.labelText`, and the
`FONT` used only by the chip text).

**2. Risk / Reward / R:R moved out of the chart footer into the position's details
grid**, as a third row directly under Entry / Size / Margin.

The figures are computed inside `ChartPanel` from the chart payload's overlay, so
they could not simply be re-rendered elsewhere. `ChartPanel` now accepts an
optional `onStats` callback; supplying it is what moves them — the caller takes
them over and the strip under the chart is not rendered. Callers that pass nothing
(Orders, StrategyTree, AiSignalLog) are unchanged.

`Positions.tsx` holds them in state and keeps them **after the chart is collapsed
again**. They describe the position, not the chart, and a figure that vanishes when
you close a panel is worse than one that stays.

**3. "Price moved" deliberately stayed with the chart.** It counts how many times a
resting order was re-priced, which is narration for the staircase drawn above it
and means nothing away from the picture. Keeping it out of the grid also keeps that
row at the three cells `DataGrid` is built for — four would have squeezed
`8× (partly recorded)` into a quarter of a phone's width, and the cells are
`nowrap`.

## A pre-existing broken build, found on the way

`./scripts/redeploy.sh dashboard-ui` could not have succeeded for anyone before
this. `npm run build` runs `tsc && vite build`, and `tsc` was already failing on
two nullable-renderer errors in the primitive's test file, introduced by `91293a7`
("chart: record every price a resting order held"):

```
$ git stash && npx tsc --noEmit
src/charts/adapters/lightweightCharts/__tests__/riskRewardPrimitive.test.ts(65,3): error TS2531: Object is possibly 'null'.
src/charts/adapters/lightweightCharts/__tests__/riskRewardPrimitive.test.ts(190,5): error TS2531: Object is possibly 'null'.
```

`vitest` never caught it because it does not type-check. The live container was
still serving a bundle built on Jul 27 08:34, before that commit.

Fixed here (`renderer()!`) because nothing could ship otherwise — the two call
sites are test harnesses where the renderer is never null.

**My own mistake worth recording:** I reported "type-check clean" from a `tsc` run
that had been backgrounded and finished *before* my last two edits. It was the
Docker build that caught the resulting error
(`Property 'color' does not exist on type '{ label: string; value: string; }'`).
The lesson is to re-check after the final edit, not to trust an earlier pass.

## Verification

```
$ npx tsc --noEmit
(no output)

$ npx vitest run
 ✓ src/charts/core/__tests__/riskReward.test.ts (35 tests)
 ✓ src/charts/adapters/lightweightCharts/__tests__/riskRewardPrimitive.test.ts (7 tests)
 Test Files  2 passed (2)
      Tests  42 passed (42)
```

The label test was replaced with one pinning the new behaviour:

```ts
it('writes no text over the candles', () => {
  const { texts } = render(model({ segments: [...] }));
  expect(texts).toEqual([]);
});
```

Deployed and checked against the running container, not the host build:

```
$ ./scripts/redeploy.sh dashboard-ui
   live dashboard-ui asset: index-BRZ53ip7.js
✓ dashboard-ui redeployed.

$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-BRZ53ip7.js
```

Served bundle, counting the strings that distinguish the two designs:

```
R:R            1     <- the grid label survives (the chip template 'R:R ' is gone: 0)
Reward         1     <- grid label
Price moved    1     <- still under the chart, as intended
TP             1     <- price-axis tag
ENT            1     <- price-axis tag
axisLabelVisible     <- axis tags still enabled
```

## Scope note

Only the Positions page moves the figures. The Orders page, the Strategy Tree's
position and order cards, and the AI signal log keep the strip under the chart —
they have no equivalent details grid to move it into, and the task was about the
position detail screen. The in-chart chip removal is shared, so every chart loses
the duplicated text, which is the point.

`decimals` is now unused inside the primitive's renderer (only the chip text
formatted prices). It is left threaded through rather than ripped out of the
adapter and two test call sites for no user-visible gain — a candidate for the next
`/simplify` pass.
