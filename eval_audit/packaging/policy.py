"""What the package keeps, and why.

Every rule here answers one question: *is this file needed to redo the
analysis on another machine?* A file that only the benchmark execution
needed (a HELM request cache, a downloaded scenario CSV) is excluded
even though it is not regenerable from the package, because nothing in
the analysis path opens it.

The rules are deliberately conservative in the other direction. Where a
directory's contents are open-ended --- a job directory's top level
accumulates new provenance files over time --- we keep *all* files at
that level rather than allowlisting names, so a future artifact is
captured by default instead of silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Absolute path roots that the packager understands and rewrites. Ordered
# longest-first so that ``/data/crfm-helm-audit-store`` wins over
# ``/data/crfm-helm-audit``; :func:`rewrite_roots` relies on that order.
#
# This list is an exact allowlist, NOT a prefix family. Official HELM run
# specs carry absolute paths from Stanford's machines (``/data/CLEAR``,
# ``/data/medhelm``, ``/data/tasks_1-20_v1-2.tmp``, ...) that do not exist
# here and must never be rewritten -- they are part of the recorded
# evidence, not references into our filesystem.
DEFAULT_SOURCE_ROOTS: tuple[str, ...] = (
    "/data/crfm-helm-audit-store",
    "/data/crfm-helm-audit",
    "/data/crfm-helm-public",
)

# Roots whose index CSVs are *catalogs* rather than work lists.
#
# ``indexes/official_public_index.csv`` enumerates all 85,025 runs in the
# public HELM mirror -- every run the pipeline could have paired against,
# not the 919 it did. Following those rows would drag in the entire
# ~491 GB mirror. A public run is an analysis input when a core-report
# packet actually references it (via ``components_manifest.json`` or a
# ``components/`` symlink); a row in a catalog is a candidate, and the
# catalog CSV itself is copied so the coverage funnel still re-renders.
#
# The local root is deliberately absent: ``audit_results_index.csv``
# holds our own 2033 runs and is scoped by construction, and its
# ``materialize_out_dpath`` / ``adapter_manifest_fpath`` /
# ``process_context_fpath`` columns are the only carriers for artifacts
# that live outside a run directory.
CATALOG_ONLY_ROOTS: tuple[str, ...] = ("/data/crfm-helm-public",)

# Files a core-report packet writes when the analysis *used* a run. A
# mention here means "this comparison consumed this run"; a mention
# anywhere else means at most "this run exists".
PACKET_MANIFEST_NAMES: frozenset[str] = frozenset(
    {
        "components_manifest.json",
        "comparisons_manifest.json",
        "core_metric_report.json",
        "comparison_intents.json",
    }
)

#: Carriers strong enough to justify copying a :data:`CATALOG_ONLY_ROOTS`
#: artifact. Everything else --- index CSVs, ``filter_inventory.json``,
#: prose in reports --- enumerates the corpus rather than consuming it.
#:
#: Keying this on *semantics* rather than file format matters: an earlier
#: version treated only CSVs as weak, which let ``filter_inventory.json``
#: (a Stage-1 catalog that happens to be JSON) pull 50,272 public runs
#: and 801 GB of HEIM image-generation output into the plan.
STRONG_CARRIERS: frozenset[str] = frozenset({"symlink", "packet"})

# Suffixes scanned for embedded absolute paths during the rewrite pass.
TEXT_SUFFIXES: frozenset[str] = frozenset(
    {".json", ".jsonl", ".txt", ".csv", ".tsv", ".yaml", ".yml", ".sh", ".md", ".html"}
)

# Dropped everywhere: derived plot rasters (every core-report directory
# ships ``redraw_plots.sh``, and the aggregate summary regenerates its
# sidecars from the ``.html`` we keep), plus editor/interpreter litter.
REGENERABLE_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg"})
JUNK_NAMES: frozenset[str] = frozenset({"__pycache__", ".DS_Store", ".ipynb_checkpoints"})

# Job-directory subtrees never entered. ``benchmark_output/`` holds the
# downloaded scenario data HELM needed to *execute* (winogrande_*.csv,
# wilds_civil_comments.csv, race.csv: ~21 GB, ~178x duplicated) and
# ``prod_env/`` holds nothing but the sqlite request caches (~20 GB, no
# two alike). The analysis reads neither. ``benchmark_output/runs/`` is
# the exception -- those are the run directories, and they are packaged
# via their own references rather than by walking the job directory.
JOB_SKIP_DIRS: frozenset[str] = frozenset({"benchmark_output", "prod_env"})

# Files that identify a kwdagger job directory. Each of these is present
# in 1403/1403 of the job directories this corpus references, whereas the
# presence of a ``benchmark_output/`` subdirectory is *not* sufficient:
# a public suite root (``/data/crfm-helm-public/mmlu``) has one too, and
# matching on it misclassifies a whole suite as a job.
JOB_MARKERS: frozenset[str] = frozenset(
    {"job_config.json", "invoke.sh", "adapter_manifest.json"}
)

# Files that identify a HELM run directory: the analysis's actual input.
RUN_MARKERS: frozenset[str] = frozenset(
    {"run_spec.json", "scenario_state.json", "per_instance_stats.json"}
)

# Experiment-level directories worth keeping beside ``helm/``.
# ``materialized_run_specs/`` is the from-spec input the index points at
# via ``materialize_out_dpath``; without it a from-spec replay cannot be
# audited. Both are small (~230 KB per experiment).
EXPERIMENT_KEEP_DIRS: frozenset[str] = frozenset(
    {"materialized_run_specs", "_kwdagger_schedule"}
)


@dataclass(frozen=True)
class Decision:
    """Why a path was kept or dropped, for the audit trail."""

    keep: bool
    reason: str


@dataclass
class DropLog:
    """Accumulates every exclusion so the package can justify its own size."""

    rows: list[tuple[str, str, int]] = field(default_factory=list)
    total_bytes: int = 0

    def record(self, path: Path, reason: str, n_bytes: int) -> None:
        self.rows.append((str(path), reason, n_bytes))
        self.total_bytes += n_bytes

    def write_tsv(self, fpath: Path) -> None:
        lines = ["path\treason\tbytes"]
        lines += [f"{p}\t{r}\t{n}" for p, r, n in sorted(self.rows)]
        fpath.write_text("\n".join(lines) + "\n", encoding="utf-8")


def classify_analysis_file(fpath: Path) -> Decision:
    """Keep everything in an analysis directory except derived rasters."""
    if fpath.name in JUNK_NAMES:
        return Decision(False, "junk")
    if fpath.suffix.lower() in REGENERABLE_SUFFIXES:
        # Only drop a raster when the means of redrawing it travels with
        # it. A stray .png with no renderer beside it is the only copy.
        if _has_renderer(fpath.parent):
            return Decision(False, "regenerable_plot")
        return Decision(True, "raster_without_renderer")
    return Decision(True, "analysis_artifact")


def _has_renderer(dpath: Path) -> bool:
    """True when this directory ships a script that redraws its plots."""
    for name in ("redraw_plots.sh", "render_heavy_pairwise_plots.sh", "reproduce.sh"):
        if (dpath / name).exists():
            return True
    # Aggregate-summary sankey/curve rasters are .jpg sidecars of a .html
    # source that the summary builder re-renders from.
    return any(dpath.glob("*.html"))


def rewrite_roots(roots: tuple[str, ...] = DEFAULT_SOURCE_ROOTS) -> tuple[str, ...]:
    """Source roots ordered longest-first for unambiguous prefix matching."""
    return tuple(sorted(roots, key=len, reverse=True))
