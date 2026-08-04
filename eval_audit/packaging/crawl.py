"""Stage 1: enumerate the analyses in a store.

This stage resolves nothing and copies nothing. It walks a store, decides
what is an *analysis*, and writes one JSON record per analysis to a JSONL
inventory whose ``include`` flag a human edits before packaging.

The flat file exists precisely so the scope decision is reviewable and
diffable rather than buried in packager logic. Two crawls of the same
store produce byte-identical inventories except for ``crawled_utc``.

Detection is by marker file, not by path shape, because the same analysis
shape appears at two different depths: a standalone experiment keeps its
reports at ``analysis/experiments/<name>/core-reports/`` while a virtual
experiment nests them one level deeper at
``virtual-experiments/<name>/analysis/core-reports/``.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from loguru import logger

from eval_audit.packaging.policy import JUNK_NAMES
from eval_audit.packaging.refs import walk

SCHEMA_VERSION = 1


@dataclass
class AnalysisRecord:
    """One packageable analysis unit."""

    id: str
    kind: str
    path: str
    #: Relative to the store root, so the inventory reads the same on any host.
    rel_path: str
    include: bool = True
    #: Free-text, set by hand: "current", "stale:<why>", "unverified".
    freshness: str = "unverified"
    n_packets: int = 0
    n_files: int = 0
    n_bytes: int = 0
    n_symlinks: int = 0
    n_broken_symlinks: int = 0
    generated_utc: str | None = None
    description: str | None = None
    status: str = "ok"
    notes: list[str] = field(default_factory=list)


# (kind, parent directory relative to store root, marker that must exist
# inside each child). A marker of "" means the child itself is the unit.
_CHILD_KINDS: tuple[tuple[str, str, str], ...] = (
    ("experiment", "analysis/experiments", "experiment_summary.json"),
    ("virtual-experiment", "virtual-experiments", "manifest.yaml"),
    ("deployment-match", "deployment-match", ""),
    ("open-judge", "open-judge", ""),
    ("store-report", "reports", ""),
)

# Whole subtrees packaged as a single unit.
_SINGLETON_KINDS: tuple[tuple[str, str], ...] = (
    ("era-tests", "analysis/era-tests"),
    ("era-tests", "indexes/era-tests"),
    ("store-index", "indexes"),
    ("store-config", "configs"),
    ("local-bundle", "local-bundles"),
    ("scenario-cache", "scenario-cache"),
)

# Loose files at ``analysis/`` that are inputs to the reporting layer.
_LOOSE_ANALYSIS_FILES: tuple[str, ...] = (
    "analysis/filter_inventory.json",
    "analysis/local_model_helm_dataset_detail.csv",
    "analysis/local_model_helm_dataset_summary.csv",
)


def crawl_store(store_dpath: Path) -> list[AnalysisRecord]:
    """Enumerate every analysis unit under ``store_dpath``."""
    store_dpath = store_dpath.resolve()
    records: list[AnalysisRecord] = []
    seen: set[Path] = set()

    for kind, parent_rel, marker in _CHILD_KINDS:
        parent = store_dpath / parent_rel
        if not parent.is_dir():
            continue
        try:
            children = sorted(p for p in parent.iterdir() if p.is_dir())
        except OSError as exc:
            logger.warning(f"cannot list {parent}: {exc}")
            continue
        for child in children:
            if child.name in JUNK_NAMES:
                continue
            if marker and not (child / marker).exists():
                logger.debug(f"skipping {child}: no {marker}")
                continue
            if child in seen:
                continue
            seen.add(child)
            records.append(_build_record(kind, child, store_dpath))

    for kind, rel in _SINGLETON_KINDS:
        dpath = store_dpath / rel
        if not dpath.is_dir() or dpath in seen:
            continue
        seen.add(dpath)
        records.append(_build_record(kind, dpath, store_dpath, subtract=seen))

    for rel in _LOOSE_ANALYSIS_FILES:
        fpath = store_dpath / rel
        if fpath.exists():
            records.append(_build_record("analysis-input", fpath, store_dpath))

    records.sort(key=lambda r: (r.kind, r.id))
    return records


def _build_record(
    kind: str,
    path: Path,
    store_dpath: Path,
    *,
    subtract: set[Path] | None = None,
) -> AnalysisRecord:
    rel = path.relative_to(store_dpath).as_posix()
    record = AnalysisRecord(
        id=f"{kind}:{rel}",
        kind=kind,
        path=str(path),
        rel_path=rel,
    )
    if path.is_file():
        try:
            record.n_files, record.n_bytes = 1, path.stat().st_size
        except OSError as exc:
            record.status = "unreadable"
            record.notes.append(str(exc))
        return record

    _measure(record, path, subtract=subtract)
    _read_provenance(record, path)
    return record


def _measure(
    record: AnalysisRecord, dpath: Path, *, subtract: set[Path] | None = None
) -> None:
    """Count files, bytes, symlinks and packets under ``dpath``.

    ``subtract`` excludes nested units already claimed by another record
    (``indexes/era-tests`` inside ``indexes/``), so the inventory's byte
    totals sum to the store rather than double-counting.
    """
    excluded = tuple(subtract or ())
    for entry in walk(dpath):
        if excluded and any(
            entry == other or other in entry.parents for other in excluded if other != dpath
        ):
            continue
        if entry.is_symlink():
            record.n_symlinks += 1
            if not entry.exists():
                record.n_broken_symlinks += 1
            continue
        try:
            if entry.is_dir():
                if entry.name.startswith("core-metrics-"):
                    record.n_packets += 1
                continue
            record.n_files += 1
            record.n_bytes += entry.stat().st_size
        except OSError as exc:
            record.status = "partial"
            if len(record.notes) < 5:
                record.notes.append(f"unreadable: {entry}: {exc}")


def _read_provenance(record: AnalysisRecord, dpath: Path) -> None:
    """Pull generation time and description from whichever marker exists."""
    for rel in ("provenance.json", "analysis/provenance.json", "experiment_summary.json"):
        fpath = dpath / rel
        if not fpath.exists():
            continue
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            record.description = record.description or data.get("description")
            record.generated_utc = record.generated_utc or (
                data.get("generated_utc") or data.get("timestamp")
            )
    if record.generated_utc is None:
        # Fall back to the newest core report, which dates the analysis
        # far better than the directory mtime does.
        newest = 0.0
        for name in ("analysis/core-reports", "core-reports"):
            candidate = dpath / name
            if candidate.is_dir():
                try:
                    newest = max(
                        (p.stat().st_mtime for p in candidate.iterdir()), default=0.0
                    )
                except OSError:
                    pass
                break
        if newest:
            record.generated_utc = datetime.fromtimestamp(
                newest, tz=timezone.utc
            ).strftime("%Y%m%dT%H%M%SZ")


def write_inventory(records: list[AnalysisRecord], fpath: Path, store_dpath: Path) -> None:
    """Write the JSONL inventory, header record first."""
    fpath.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "header",
        "store_dpath": str(store_dpath),
        "crawled_utc": datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "n_analyses": len(records),
        "n_bytes": sum(r.n_bytes for r in records),
    }
    with fpath.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(header, sort_keys=True) + "\n")
        for record in records:
            row = {"record_type": "analysis", **asdict(record)}
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def read_inventory(fpath: Path) -> tuple[dict, list[AnalysisRecord]]:
    """Read a (possibly hand-edited) inventory back."""
    header: dict = {}
    records: list[AnalysisRecord] = []
    with fpath.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{fpath}:{line_no}: not valid JSON: {exc}") from exc
            if row.get("record_type") == "header":
                header = row
                continue
            row.pop("record_type", None)
            known = {f for f in AnalysisRecord.__dataclass_fields__}
            records.append(AnalysisRecord(**{k: v for k, v in row.items() if k in known}))
    return header, records


def summarize(records: list[AnalysisRecord]) -> str:
    """A short human-readable digest of a crawl."""
    by_kind: dict[str, tuple[int, int, int]] = {}
    for record in records:
        n, files, size = by_kind.get(record.kind, (0, 0, 0))
        by_kind[record.kind] = (n + 1, files + record.n_files, size + record.n_bytes)
    width = max((len(k) for k in by_kind), default=4)
    lines = [f"{'kind':<{width}}  {'units':>5}  {'files':>8}  {'size':>10}"]
    for kind in sorted(by_kind):
        n, files, size = by_kind[kind]
        lines.append(f"{kind:<{width}}  {n:>5}  {files:>8}  {size / 1e9:>9.2f}G")
    total_bytes = sum(r.n_bytes for r in records)
    broken = sum(r.n_broken_symlinks for r in records)
    lines.append(f"{'TOTAL':<{width}}  {len(records):>5}  "
                 f"{sum(r.n_files for r in records):>8}  {total_bytes / 1e9:>9.2f}G")
    if broken:
        lines.append(f"broken symlinks in store: {broken}")
    return "\n".join(lines)


def iter_included(records: list[AnalysisRecord]) -> Iterator[AnalysisRecord]:
    for record in records:
        if record.include:
            yield record
