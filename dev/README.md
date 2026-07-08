# Development artifacts

This directory holds development-support material that is not part of the
installed `eval_audit` package:

- `analysis/` — refactor maps and validation write-ups
- `e2e-tests/` — numbered end-to-end shell checks (env, smoke grid, index, compose)
- `journals/` — append-only agent/human session journals
- `lessons/` — accumulated lessons learned
- `paper-analysis/` — one-shot, machine-specific analysis scripts backing paper claims (moved from `docs/papers/`)
- `scripts/` — submodule helpers and ad hoc catalog scripts
- `tools/` — standalone dev tools (e.g. `deployment_match`)

The operational pipeline itself lives in the top-level `README.md`,
`eval_audit/`, and `configs/`.
