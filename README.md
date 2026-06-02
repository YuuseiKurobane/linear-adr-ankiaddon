# Linear ADR Anki Add-on

Linear ADR adds an `ADR` menu to Anki for exporting deck behavior, running the
bundled Linear ADR optimizer, saving optimized scheduling policies, and applying
those policies to review cards.

## Main Workflow

1. Use `ADR -> Write button usage file` to export one JSONL row per selected
   deck options preset.
2. Use `ADR -> Optimize ADR Parameters` to run the bundled `adr-optimizer`
   helper against that export.
3. Review the optimizer output and save selected preset policies as a new car.
4. Use `ADR -> Generate combined filtered deck`, or the deck gear menu's
   `ADR Helper -> Generate filtered deck`, to collect cards that are due under
   active car policies.
5. Optionally use `ADR Helper -> Reschedule all cards` to write ADR intervals
   directly back to cards in a deck scope.

## Features

- Exports button usage inputs grouped by effective deck options preset, including
  FSRS-6 weights, desired retention, deck/card counts, rating probabilities,
  review and learning costs, and summary stats.
- Runs a native Rust optimizer from `helper/<platform>/adr-optimizer`, with
  `ADR_OPTIMIZER_BINARY` available as an override.
- Supports multi-preset batch optimization with per-preset target DR, scheduling
  point selection (`Recommended`, `Aggressive`, or `Calm`), and built-in or custom
  quality presets.
- Shows optimizer progress and opens generated HTML Pareto reports from
  `outputs/`.
- Lets saved policies use ADR parameters, a fixed desired-retention target, or
  normal Anki scheduling on a per-preset basis.
- Manages active cars, including moving a car's position date, archiving a car by
  driving it to the beginning, deleting a car, and preserving archived history
  when enabled.
- Generates filtered decks from active car policies, with configurable deck name,
  card limit, sort order, filtered-deck rescheduling, and optional backward
  driving of the oldest car to fill review volume.
- Reschedules review cards by computing intervals from card memory state,
  selected policy, and the optional soft interval cap.
- Provides deck-scoped actions in each deck's gear menu and collection-wide
  actions from the top-level `ADR` menu.

## Cars

A car is a saved snapshot of scheduling policies produced from an optimizer run.
Each active car has a `position_date`. A review card whose latest review is after
that position can be handled by that car, as long as the car has a matching deck
preset policy.

When more than one active car can handle a card, the add-on now chooses the car
with the closest earlier `position_date`; `created_at` is only a tie-breaker.
That means cars can overtake each other when their positions are moved. A newly
created car still behaves as expected when positions are in creation order, but
manual movement is no longer overridden by creation date.

Filtered deck generation can also drive the oldest active car backward. If too
few cards are currently due, the add-on looks for older cards that would be due
under that car, includes enough of them to fill the requested limit, and then
moves the car behind the selected cards once the filtered deck is created.

## Local Files

- `exports/adr-input-*.jsonl`: button usage exports consumed by the optimizer.
- `outputs/`: optimizer summaries, HTML reports, batch configs, and batch
  summaries.
- `cars.json`: active cars, archived car history, and filtered-deck transactions.
- `config.json`: add-on defaults for warnings, history tracking, soft interval
  cap behavior, custom quality presets, and filtered-deck options.
- `helper/`: bundled optimizer binaries for supported desktop platforms.

## Notes

The add-on only considers review cards (`type = 2`) that are active, not already
inside a filtered deck, and have a valid review log entry. Policies are matched
by deck options preset id first, then preset name. `Normal Anki` policies are
treated as explicit skips for ADR rescheduling.
