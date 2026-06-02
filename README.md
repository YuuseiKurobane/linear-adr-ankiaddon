Original concept by 1DWalker: [srs-simulator on the `fsrs-sa` branch](https://github.com/1DWalker/srs-simulator/tree/fsrs-sa).

Rust helper files source: [YuuseiKurobane/linear-adr-ankiaddon](https://github.com/YuuseiKurobane/linear-adr).

Legacy Python version: [YuuseiKurobane/linear-adr-optimizer-python](https://github.com/YuuseiKurobane/linear-adr-optimizer-python).

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
  `outputs/`, where a single-preset report can create a new car or append the
  chosen policy to the latest-position active car.
- Lets saved policies use ADR parameters, a fixed desired-retention target, or
  normal Anki scheduling on a per-preset basis.
- Manages active cars, including moving a car's position date, driving a car to
  timeline start, restoring archived cars when history is enabled, and deleting
  a car.
- Generates filtered decks from active car policies, with configurable deck name,
  sort order, filtered-deck rescheduling, date-based newest-car movement, and
  workload previews.
- Reschedules review cards by computing intervals from card memory state,
  selected policy, FSRS-style fuzz/load balancing, and the optional soft
  interval cap.
- Provides deck-scoped actions in each deck's gear menu and collection-wide
  actions from the top-level `ADR` menu.

## Cars

A car is a saved snapshot of scheduling policies produced from an optimizer run.
Each active car has a `position_date`. A review card whose latest review is after
that position can be handled by that car, as long as the car has a matching deck
preset policy.

When more than one active car can handle a card, the add-on now chooses the car
with the closest earlier `position_date`; `created_at` is only a tie-breaker.
Scheduling operations move the latest-position car, so manual movement is not
overridden by creation date.

Scheduling operations drive the latest-position active car backward with a
date-based slider. The slider controls the car's position on the review
timeline, not an exact card count. Moving the car farther back expands the
historical range that can be handled by that car.

When the moving latest-position car crosses older cars, those older cars are
removed from active scheduling. If history tracking is enabled, swept cars are
archived and can be restored from `ADR Helper -> Manage cars -> Manually manage
cars`; otherwise they are discarded. Anki's normal undo can undo card
rescheduling, but car timeline changes are restored from `ADR Helper -> Manage
cars -> Manually manage cars -> Undo last car change`.

The single-preset Pareto report offers `Create car` and `Append policy to latest
car`. "Latest" means the active car with the newest position date, not the newest
creation timestamp. This is intentional so users who manually reorder cars can
append missing preset policies to the car that is currently furthest along the
timeline. Appending a policy replaces the existing policy for that preset in the
latest-position car after a warning; use `Create car` when the goal is to start
a new scheduling era.

## Local Files

- `exports/adr-input-*.jsonl`: button usage exports consumed by the optimizer.
- `outputs/`: optimizer summaries, HTML reports, batch configs, and batch
  summaries.
- `cars.json`: active cars, archived car history, and filtered-deck transactions.
- `config.json`: add-on defaults for warnings, history tracking, soft interval
  cap behavior, custom quality presets, filtered-deck options, timeline
  subdivisions, and load-balance powers.
- `helper/`: bundled optimizer binaries for supported desktop platforms.

## Notes

The add-on only considers review cards (`type = 2`) that are active, not already
inside a filtered deck, and have a valid review log entry. Policies are matched
by deck options preset id first, then preset name. `Normal Anki` policies are
treated as explicit skips for ADR rescheduling.
