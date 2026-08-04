"""Transfer-archive packaging: carve out what is needed to redo the analysis.

Two stages, deliberately separated by a hand-editable flat file:

1. :mod:`eval_audit.packaging.crawl` walks a store and enumerates every
   *analysis* it finds, writing one JSON record per analysis to a JSONL
   inventory. It resolves nothing and copies nothing --- its only job is
   to produce the decision surface a human edits before packaging.

2. :mod:`eval_audit.packaging.pack` consumes that inventory, follows every
   external reference out of the included analyses, deduplicates the
   referenced artifacts, copies them under one package root, and rewrites
   the absolute paths so the package is navigable on another machine.

The retention policy (:mod:`eval_audit.packaging.policy`) is grounded in
what the *analysis* reads, not in what the benchmark run needed to
execute. Execution state --- HELM request caches, downloaded scenario
data --- is excluded by design: the package exists to redo the analysis
elsewhere, not to re-run the benchmark.
"""
from __future__ import annotations
