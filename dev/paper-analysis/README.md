# Paper analysis scripts

One-shot analysis scripts written to support specific paper claims. These are
**not** part of the installed `eval_audit` package and are not exercised by the
test suite or runbooks — they bake in machine-specific absolute `/data` paths
and were run by hand once to produce figures/tables for a draft.

Moved here from `docs/papers/neurips-2026/` (source docs shouldn't carry
executable analysis code).

- `neurips-2026/measure_wikifact_logits.py` — measures WikiFact completion
  logits behind the Case Study 3 appendix consistency claim.
- `neurips-2026/wikifact_two_run_prediction.py` — two-run prediction audit
  built on the logits emitted by the script above.
