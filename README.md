# Linear ADR Anki Add-on

Adds an `ADR` menu to Anki with:

- `Write button usage file`
- `Simulate Preset with button usage file`

The simulation UI calls the bundled Rust `adr-optimizer` helper from
`helper/<platform>/` and writes result files to `outputs/`.
