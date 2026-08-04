# Transfer packaging

A standalone tool, not a pipeline stage. It reads a finished store and
carves out what is needed to **redo the analysis** on another machine.
Nothing else in `eval_audit` depends on it, and it depends on the rest
only for the shape of what it finds on disk.

It is not a backup, and it is not a way to re-run the benchmarks. The
package deliberately excludes everything HELM needed in order to
*execute*, because none of it is read when the analysis runs.

## Two stages, separated by a flat file

The intermediate JSONL is the point of the split: it makes the scope
decision reviewable and diffable instead of buried in packager logic.

```bash
# 1. enumerate the analyses
eval-audit-crawl-analyses \
  --store-dpath /data/crfm-helm-audit-store \
  --out-fpath analysis_inventory.jsonl

# 2. edit analysis_inventory.jsonl by hand:
#      "include": false      -- leave this analysis out
#      "freshness": "stale:pre-a25aac9"  -- label what you know

# 3. see what that would cost before copying anything
eval-audit-package-analyses \
  --inventory-fpath analysis_inventory.jsonl \
  --package-dpath /scratch/eval-audit-package --dry-run

# 4. build it (resumable -- safe to re-run after an interruption)
eval-audit-package-analyses \
  --inventory-fpath analysis_inventory.jsonl \
  --package-dpath /scratch/eval-audit-package
```

Re-crawling preserves hand edits by analysis id; `--reset` discards them.
Always dry-run first: the plan reports the byte total, and the copy is
the expensive half.

## What the crawler counts as an analysis

Detection is by marker file rather than path shape, because the same
analysis shape sits at two different depths --- a standalone experiment
keeps reports at `analysis/experiments/<name>/core-reports/`, a virtual
experiment one level deeper at `virtual-experiments/<name>/analysis/`.

| kind | marker |
|---|---|
| `experiment` | `analysis/experiments/*/experiment_summary.json` |
| `virtual-experiment` | `virtual-experiments/*/manifest.yaml` |
| `deployment-match` | `deployment-match/*` |
| `open-judge` | `open-judge/*` |
| `store-report` | `reports/*` |
| `era-tests`, `store-index`, `store-config`, `local-bundle`, `scenario-cache`, `analysis-input` | fixed locations |

## What travels

Everything an included analysis references: local and official run
directories, job-level provenance, `materialized_run_specs/`, and any EEE
artifact an analysis actually names.

References leave an analysis by five carriers, and the extractor handles
all of them --- `components/` symlinks, JSON manifests, index CSV columns,
generated shell scripts, and prose in text summaries. JSON is walked
structurally rather than by a key allowlist, so a path under a field this
code has never heard of is still found.

## What does not, and why

| excluded | size | reason |
|---|---|---|
| `prod_env/cache/*.sqlite` | ~20 GB | HELM request caches; no analysis code opens them |
| `benchmark_output/{scenarios,scenario_instances}/` | ~21 GB | datasets HELM downloaded in order to execute |
| `eee/by-run-path/` | 71 GB | derived conversion cache; referenced by no analysis |
| `*.png` / `*.jpg` beside a `redraw_plots.sh` | ~1.2 GB | regenerable from the renderer that ships with them |
| public runs known only from a catalog row | ~491 GB | see below |

**The catalog rule is the load-bearing one.**
`indexes/official_public_index.csv` enumerates all 85,025 runs in the
public mirror --- every run the pipeline *could* have paired against, not
the ~919 it did. A public run is packaged when a core-report packet
references it; a catalog row alone is a candidate, and the catalog CSV
itself is copied so the coverage funnel still re-renders. Without this
rule the packager follows an index into half a terabyte.

The local index is deliberately exempt: `audit_results_index.csv` is
scoped to our own 2033 runs, and its `materialize_out_dpath` /
`adapter_manifest_fpath` / `process_context_fpath` columns are the only
carriers for artifacts that live outside a run directory.

No exclusion is silent. Every dropped file lands in `drops.tsv` with its
size and reason; the catalog skip count lands in `MANIFEST.json`.

## Layout, and why it mirrors instead of reorganising

```
package/
  MANIFEST.json              analyses, artifacts, counts, findings
  rewrites.json              every path substitution, and where
  pre_rewrite_hashes.json    pre-rewrite SHA-256 per rewritten file
  drops.tsv                  every excluded file, reason, bytes
  missing.tsv                references that did not resolve, typed
  REPACK.md                  instructions for the receiving machine
  root/
    data/crfm-helm-audit-store/...
    data/crfm-helm-audit/...
    data/crfm-helm-public/...
```

`root/` mirrors absolute source paths verbatim. That is not laziness: the
`components/` symlinks inside every core-report packet are *relative* and
depth-coupled (`../../../../../../../crfm-helm-audit/...`), valid only
because the packet sits at a known depth below a store root that has
`crfm-helm-audit` as a sibling. Preserve that shape and all 7619 of them
resolve with no rewriting at all. A rewritten symlink that resolves
nowhere still looks like a symlink, so not having to touch them removes
a whole class of silent data loss.

Deduplication falls out of the same choice: two analyses referencing one
run directory map to the same destination and it is copied once.

## Rewriting, and getting back

Absolute paths embedded in JSON, CSV and shell are rewritten to the build
location. `rewrites.json` records every substitution and
`pre_rewrite_hashes.json` each file's SHA-256 as it was in the store, so
the transform is invertible and each file checkable against its original.

If the package is extracted somewhere other than where it was built:

```bash
eval-audit-package-analyses --repoint /new/location/eval-audit-package
```

The rewriter takes an **exact allowlist of source roots**, never a `/data/`
prefix family. Official HELM run specs carry absolute paths from Stanford's
machines --- `/data/CLEAR`, `/data/medhelm`, `/data/tasks_1-20_v1-2.tmp` ---
which do not exist here and are recorded evidence, not references. They are
left alone.

## Verification

After copying, the packager checks that every symlink resolves, that none
escape the package root, and that no bare source root survives the rewrite.
Findings carry a severity, because two different things present identically
as a broken link:

- **error** --- a link that resolved in the store and does not resolve here.
  This is a packager defect and fails the build.
- **note** --- a link that was *already* broken in the store. This corpus
  ships 83 of them; they are part of the record being preserved.

`missing.tsv` types unresolvable references the same way.
`absent_local_mirror_may_exist_upstream` matters because
`/data/crfm-helm-public` here is a partial rsync --- a file's absence
locally does not mean it was absent upstream.

## Operational notes

- The copy is resumable: a destination file matching on size is skipped,
  so an interrupted run picks up where it stopped.
- `EMFILE` is retried with backoff. This filesystem intermittently
  exhausts file descriptors under load, and a packager that died on the
  first `OSError` would never finish.
- A directory that cannot be listed costs only that directory, not the
  traversal --- partial results that say so beat an abort.
