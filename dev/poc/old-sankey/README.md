# old-sankey

Snapshot of `magnet/utils/sankey.py` as it stood when the helm-audit /
aiq-magnet split was finalized. Preserved here so the older Plan / Root
/ Group / Bucket / Split DSL is not lost if we ever want to resurrect
it.

The active sankey utilities now live in `eval_audit/utils/`:

- `eval_audit/utils/sankey.py` — `emit_sankey_artifacts` wrapper
  (HTML + JPG sidecar emission, used by the reporting workflows).
- `eval_audit/utils/sankey_builder.py` — fluent Sankey spec builder
  (`Root`, `Group`, `Constant`, `SankeyDiGraph.demo()` etc.). This is
  the supported API; the builder superseded the older DSL captured in
  this directory.

Nothing in either repo imports the snapshot — it is dead code retained
purely as historical reference.
