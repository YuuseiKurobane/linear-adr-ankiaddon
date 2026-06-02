# Linear ADR Anki Add-on

Adds an `ADR` menu to Anki with:

- `Write button usage file`
- `Optimize ADR Parameters`
- `Manage cars 🚙`
- `Generate combined filtered deck`
- `Draw pareto plot for one preset`

The optimizer UI calls the bundled Rust `adr-optimizer` helper from
`helper/<platform>/` and writes result files to `outputs/`. Active car
scheduling snapshots are stored locally in `cars.json`.
