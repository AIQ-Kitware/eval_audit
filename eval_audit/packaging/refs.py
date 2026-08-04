"""Finding every path that points out of an analysis directory.

References leave an analysis by five different carriers, and a packager
that handles only the obvious one ships a package full of links into a
filesystem the recipient does not have:

1. ``components/`` symlinks inside each core-report packet.
2. ``components_manifest.json`` --- ``run_path``, ``job_path``,
   ``eee_artifact_path``, ``report_dpath``, ``local_index_fpath``,
   ``official_index_fpath``, ``planner_artifact_fpath``.
3. ``core_metric_report.json`` --- ``report_dpath`` and three
   ``*_manifest_path`` fields; ``provenance.json`` --- ``output_root``
   and ``{audit,official}_sources[].fpath``.
4. Index CSVs --- ``run_path``, ``run_dir``, ``run_spec_fpath``,
   ``materialize_out_dpath``, ``adapter_manifest_fpath``,
   ``process_context_fpath``, ``job_dpath``, ``eee_artifact_path``.
5. Absolute paths baked into generated shell scripts and text summaries.

Rather than allowlist those keys --- which would miss the next one added
--- we walk JSON structurally, scan every CSV cell, and regex the text
files, keeping anything that starts with a known source root.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from loguru import logger

from eval_audit.packaging.policy import (
    DEFAULT_SOURCE_ROOTS,
    PACKET_MANIFEST_NAMES,
    TEXT_SUFFIXES,
    rewrite_roots,
)

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# A path-shaped token starting at one of our roots. Trailing punctuation
# is trimmed by _clean(); JSON/CSV extraction does not need the regex.
_PATH_CHARS = r"[A-Za-z0-9_.,:=@%+~/-]+"


@dataclass
class RefTable:
    """Every distinct external path an analysis set points at."""

    #: source path -> number of references to it
    counts: Counter[str] = field(default_factory=Counter)
    #: source path -> the analysis ids that reference it
    referrers: dict[str, set[str]] = field(default_factory=dict)
    #: source path -> which kinds of carrier mentioned it. A path known
    #: only from a catalog CSV is a weaker claim than one a packet's
    #: manifest or symlink points at; :mod:`eval_audit.packaging.pack`
    #: uses this to avoid following an 85,000-row index into the whole
    #: public mirror.
    carriers: dict[str, set[str]] = field(default_factory=dict)

    def add(self, path: str, analysis_id: str, n: int = 1, carrier: str = "json") -> None:
        self.counts[path] += n
        self.referrers.setdefault(path, set()).add(analysis_id)
        self.carriers.setdefault(path, set()).add(carrier)

    def __len__(self) -> int:
        return len(self.counts)

    @property
    def total_refs(self) -> int:
        return sum(self.counts.values())


def make_matcher(roots: Iterable[str] = DEFAULT_SOURCE_ROOTS) -> re.Pattern[str]:
    """A regex matching any absolute path under one of ``roots``.

    Anchored on the root and followed by a path-boundary so that
    ``/data/crfm-helm-audit-store`` does not also match as
    ``/data/crfm-helm-audit`` + ``-store``.
    """
    alts = "|".join(re.escape(r) for r in rewrite_roots(tuple(roots)))
    return re.compile(rf"(?:{alts})(?![A-Za-z0-9_-])(?:{_PATH_CHARS})?")


def _clean(raw: str) -> str:
    """Trim punctuation a path picked up from surrounding prose or JSON."""
    return raw.rstrip("\"'),;:.\\ \t")


def iter_json_paths(node: Any, matcher: re.Pattern[str]) -> Iterator[str]:
    """Yield every string anywhere in a decoded JSON document that is a path.

    Structural rather than key-driven: a new path-bearing field added to
    a manifest is picked up without touching this code.
    """
    if isinstance(node, str):
        if matcher.fullmatch(node.strip()):
            yield _clean(node.strip())
        else:
            # Paths also appear embedded in message strings (warnings,
            # caveats, reproduce commands).
            for m in matcher.finditer(node):
                yield _clean(m.group(0))
    elif isinstance(node, dict):
        for value in node.values():
            yield from iter_json_paths(value, matcher)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from iter_json_paths(value, matcher)


def carrier_of(fpath: Path) -> str:
    """Which kind of carrier this file is, for reference-strength rules.

    ``packet`` is the strong one: a core-report packet writes these when
    the analysis actually consumed a run. Every other carrier can just as
    easily be enumerating the corpus.
    """
    if fpath.name in PACKET_MANIFEST_NAMES:
        return "packet"
    suffix = fpath.suffix.lower()
    if suffix in {".json", ".jsonl"}:
        return "json"
    if suffix in {".csv", ".tsv"}:
        return "csv"
    return "text"


def extract_from_file(fpath: Path, matcher: re.Pattern[str]) -> Counter[str]:
    """Every source-root path referenced by one file."""
    found: Counter[str] = Counter()
    suffix = fpath.suffix.lower()
    try:
        if suffix in {".json", ".jsonl"}:
            found.update(_extract_json(fpath, matcher))
        elif suffix in {".csv", ".tsv"}:
            found.update(_extract_csv(fpath, matcher))
        elif suffix in TEXT_SUFFIXES:
            found.update(_extract_text(fpath, matcher))
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(f"unreadable while extracting refs: {fpath}: {exc}")
    return found


def _extract_json(fpath: Path, matcher: re.Pattern[str]) -> Counter[str]:
    found: Counter[str] = Counter()
    text = fpath.read_text(encoding="utf-8", errors="replace")
    try:
        found.update(iter_json_paths(json.loads(text), matcher))
        return found
    except json.JSONDecodeError:
        pass
    # JSONL, or a truncated document: fall back to per-line then to raw
    # regex, so a malformed file still contributes its references.
    ok = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            found.update(iter_json_paths(json.loads(line), matcher))
            ok = True
        except json.JSONDecodeError:
            continue
    if not ok:
        found.update(_clean(m.group(0)) for m in matcher.finditer(text))
    return found


def _extract_csv(fpath: Path, matcher: re.Pattern[str]) -> Counter[str]:
    """Scan every cell. Path columns vary by index schema version."""
    found: Counter[str] = Counter()
    delim = "\t" if fpath.suffix.lower() == ".tsv" else ","
    with fpath.open(newline="", encoding="utf-8", errors="replace") as handle:
        for row in csv.reader(handle, delimiter=delim):
            for cell in row:
                if not cell or "/" not in cell:
                    continue
                cell = cell.strip()
                if matcher.fullmatch(cell):
                    found[_clean(cell)] += 1
                elif cell.startswith("/"):
                    for m in matcher.finditer(cell):
                        found[_clean(m.group(0))] += 1
    return found


def _extract_text(fpath: Path, matcher: re.Pattern[str]) -> Counter[str]:
    text = fpath.read_text(encoding="utf-8", errors="replace")
    return Counter(_clean(m.group(0)) for m in matcher.finditer(text))


def extract_from_symlink(link: Path) -> str | None:
    """The absolute target of a symlink, or None when it stays inside."""
    try:
        target = (link.parent / link.readlink()).resolve(strict=False)
    except OSError:
        return None
    return str(target)


def collect_refs(
    analysis_dpath: Path,
    analysis_id: str,
    table: RefTable,
    matcher: re.Pattern[str],
    *,
    roots: tuple[str, ...] = DEFAULT_SOURCE_ROOTS,
) -> tuple[int, int]:
    """Walk one analysis directory, adding every external ref to ``table``.

    Returns ``(n_files_scanned, n_unreadable)``. Unreadable files are
    logged rather than fatal --- this filesystem intermittently exhausts
    file descriptors, and a partial scan that says so beats an abort.
    """
    n_scanned = 0
    n_unreadable = 0
    # An analysis unit can be a single loose file (``filter_inventory.json``
    # and the dataset CSVs at ``analysis/``), not only a directory.
    candidates = (
        [analysis_dpath]
        if analysis_dpath.is_file()
        else walk(analysis_dpath)
    )
    for path in candidates:
        if path.is_symlink():
            target = extract_from_symlink(path)
            if target and target.startswith(roots):
                table.add(target, analysis_id, carrier="symlink")
            continue
        if not path.is_file():
            continue
        n_scanned += 1
        carrier = carrier_of(path)
        try:
            for ref, count in extract_from_file(path, matcher).items():
                table.add(ref, analysis_id, count, carrier=carrier)
        except OSError:
            n_unreadable += 1
    return n_scanned, n_unreadable


def walk(root: Path) -> Iterator[Path]:
    """Depth-first walk that survives EMFILE and unreadable directories.

    ``Path.rglob`` aborts the whole traversal on the first ``OSError``;
    on this filesystem that loses everything after the failure. Here a
    directory we cannot open costs only that directory.
    """
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError as exc:
            logger.warning(f"cannot list {current}: {exc}")
            continue
        for entry in entries:
            yield entry
            if entry.is_dir() and not entry.is_symlink():
                stack.append(entry)
