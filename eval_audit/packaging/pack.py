"""Stage 2: carve the included analyses and their references into a package.

The package mirrors absolute source paths under a single ``root/`` tree::

    <package>/root/data/crfm-helm-audit-store/...  analyses, indexes, configs
    <package>/root/data/crfm-helm-audit/...        local runs, job provenance
    <package>/root/data/crfm-helm-public/...       referenced official runs

Mirroring rather than re-organising is what makes this tractable. The
``components/`` symlinks inside every core-report packet are *relative*
and depth-coupled (``../../../../../../../crfm-helm-audit/...``); they
are valid only because the packet sits at a known depth below a store
root that has ``crfm-helm-audit`` as a sibling. Preserve that shape and
all 7619 of them keep resolving with no rewriting at all --- and a broken
relative symlink is silent data loss, so not having to touch them is
worth more than a prettier layout.

Deduplication falls out for free: two analyses referencing the same run
directory map to the same destination path and it is copied once. In this
corpus that is a 2.8x reduction (6450 references, 2334 distinct runs).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from loguru import logger

from eval_audit.packaging.crawl import AnalysisRecord, iter_included
from eval_audit.packaging.policy import (
    CATALOG_ONLY_ROOTS,
    DEFAULT_SOURCE_ROOTS,
    EXPERIMENT_KEEP_DIRS,
    JOB_MARKERS,
    JOB_SKIP_DIRS,
    JUNK_NAMES,
    RUN_MARKERS,
    STRONG_CARRIERS,
    TEXT_SUFFIXES,
    DropLog,
    classify_analysis_file,
    rewrite_roots,
)
from eval_audit.packaging.refs import RefTable, collect_refs, make_matcher, walk

SCHEMA_VERSION = 1
#: Everything copied lands under this directory, mirroring absolute paths.
MIRROR_DIRNAME = "root"

#: Retention rules, by what the referenced path turns out to be.
RULE_WHOLE = "whole"           # copy the directory entire
RULE_JOB_TOPLEVEL = "job_toplevel"    # top-level files only, no subdirs
RULE_EXPERIMENT = "experiment_toplevel"  # files + materialized specs, skip helm/
RULE_FILE = "file"             # a single file
RULE_SKIP = "skip"             # already covered by an included analysis
RULE_CONTAINER = "container"   # an ancestor, not a unit: children arrive on their own

#: A recognized unit above this size is copied but shouted about; an
#: *unrecognized* directory is never copied whole at any size.
WHOLE_COPY_WARN_BYTES = 5 * 1024**3


@dataclass
class Artifact:
    """One deduplicated external unit to copy."""

    src: str
    rule: str
    n_refs: int
    referrers: int
    n_files: int = 0
    n_bytes: int = 0
    status: str = "ok"


@dataclass
class PackagePlan:
    analyses: list[AnalysisRecord] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    missing: list[tuple[str, str, int]] = field(default_factory=list)
    ref_table: RefTable = field(default_factory=RefTable)
    #: catalog rows deliberately not followed (see :func:`_is_catalog_only`)
    catalog_only: int = 0
    #: referenced ancestors that are containers rather than units
    containers: int = 0

    @property
    def n_bytes(self) -> int:
        return sum(a.n_bytes for a in self.artifacts) + sum(
            a.n_bytes for a in self.analyses
        )


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------


def _is_execution_state(path: Path) -> bool:
    """True for paths inside a job's execution state, at any depth.

    Skipping a job directory's subtrees is not enough on its own: a
    report can reference ``.../helm_id_x/prod_env/cache`` *directly*, and
    such a path reaches classification without ever passing through the
    job-directory rule. It is also a leaf, so the leaf fallback would
    happily copy it whole --- which is how a sqlite request cache turned
    up in a plan that excludes sqlite request caches.

    ``benchmark_output/runs/`` is the one permitted subtree: run
    directories live there and are the analysis's real inputs.
    """
    parts = path.parts
    if "prod_env" in parts:
        return True
    if "benchmark_output" in parts:
        tail = parts[parts.index("benchmark_output") + 1:]
        # ``benchmark_output`` itself is an ordinary container; only its
        # non-``runs`` children are execution state.
        return bool(tail) and tail[0] != "runs"
    return False


def classify_ref(path: Path, included_roots: Iterable[Path]) -> str:
    """Decide how much of a referenced path to copy, by what it contains.

    Detection is by *content*, never by path shape. An earlier version
    inferred structure from a ``helm`` path component and defaulted
    unrecognized directories to a whole copy; because an experiment
    directory is ``/data/crfm-helm-audit/<exp>`` --- no ``helm``
    component --- every referenced container fell through to "copy
    everything beneath me", and the plan came to 1168 GB instead of ~27.

    The rule that prevents a recurrence is the default: an unrecognized
    directory is a **container**, not a unit. Its children arrive through
    their own references, so skipping it loses nothing, whereas copying
    it whole silently drags in a subtree of arbitrary size.

    The distinction that does the real work is the job directory. Its top
    level is provenance the analysis reads (``container_provenance.json``,
    ``process_context.json``, ``adapter_manifest.json``,
    ``job_config.json``, ``helm-run.log``) and costs ~213 MB across the
    corpus. Its subtrees are execution state no analysis opens:
    ``benchmark_output/`` holds ~21 GB of downloaded scenario data,
    ``prod_env/`` ~20 GB of sqlite request caches.
    """
    for root in included_roots:
        if path == root or root in path.parents:
            return RULE_SKIP
    if _is_execution_state(path):
        return RULE_SKIP
    if path.is_file():
        return RULE_FILE
    if not path.is_dir():
        return RULE_SKIP

    # A HELM run directory: the analysis's actual input.
    if any((path / marker).exists() for marker in RUN_MARKERS):
        return RULE_WHOLE
    # A job directory: keep its provenance, never enter its subtrees.
    # Matched on kwdagger's own markers, not on the presence of a
    # ``benchmark_output/`` subdirectory -- a public suite root has one
    # of those too.
    if any((path / marker).exists() for marker in JOB_MARKERS):
        return RULE_JOB_TOPLEVEL
    # An experiment directory: files plus materialized specs, skip helm/.
    if (path / "helm").is_dir():
        return RULE_EXPERIMENT
    # An EEE artifact: small, self-contained, and named by a packet.
    if path.name == "eee_output" or (path / "eee_output").is_dir():
        return RULE_WHOLE
    # A from-spec input directory, always tiny.
    if "materialized_run_specs" in path.parts:
        return RULE_WHOLE

    # Fallback: a directory with no subdirectories is a leaf, so copying
    # it whole is bounded by its own files and cannot drag in a subtree.
    # This is the safety net for a run directory whose file set does not
    # match RUN_MARKERS -- losing one would be unrecoverable, while
    # copying one extra leaf costs its own size and nothing more.
    # Containers, by definition, have children and never reach here.
    try:
        if not any(child.is_dir() for child in path.iterdir()):
            return RULE_WHOLE
    except OSError as exc:
        logger.warning(f"cannot classify {path}: {exc}")
        return RULE_CONTAINER
    return RULE_CONTAINER


def build_plan(
    records: list[AnalysisRecord],
    *,
    roots: tuple[str, ...] = DEFAULT_SOURCE_ROOTS,
) -> PackagePlan:
    """Scan the included analyses and resolve everything they point at."""
    plan = PackagePlan(analyses=list(iter_included(records)))
    matcher = make_matcher(roots)

    logger.info(f"scanning {len(plan.analyses)} analyses for external references")
    for record in plan.analyses:
        dpath = Path(record.path)
        if not dpath.exists():
            record.status = "missing"
            plan.missing.append((record.path, "analysis_dir_absent", 0))
            logger.warning(f"analysis directory absent: {dpath}")
            continue
        n_scanned, n_unreadable = collect_refs(
            dpath, record.id, plan.ref_table, matcher, roots=roots
        )
        logger.debug(
            f"{record.id}: {n_scanned} files scanned, "
            f"{n_unreadable} unreadable, {plan.ref_table.total_refs} refs so far"
        )

    logger.info(
        f"{plan.ref_table.total_refs} references -> "
        f"{len(plan.ref_table)} distinct paths"
    )

    included_roots = [Path(r.path).resolve() for r in plan.analyses]
    n_catalog_only = 0
    n_container = 0
    refs = sorted(plan.ref_table.counts.items())
    for i, (src, count) in enumerate(refs):
        if i and i % 20_000 == 0:
            logger.info(
                f"classified {i}/{len(refs)} references; "
                f"{len(plan.artifacts)} artifacts, {n_catalog_only} catalog-only"
            )
        path = Path(src)
        if _is_catalog_only(src, plan.ref_table.carriers.get(src, set())):
            n_catalog_only += 1
            continue
        if not path.exists():
            plan.missing.append((src, _classify_miss(path), count))
            continue
        rule = classify_ref(path.resolve(), included_roots)
        if rule == RULE_SKIP:
            continue
        if rule == RULE_CONTAINER:
            n_container += 1
            continue
        artifact = Artifact(
            src=src,
            rule=rule,
            n_refs=count,
            referrers=len(plan.ref_table.referrers.get(src, ())),
        )
        artifact.n_files, artifact.n_bytes = _measure_artifact(path, rule)
        if artifact.n_bytes > WHOLE_COPY_WARN_BYTES:
            logger.warning(
                f"large single artifact ({artifact.n_bytes / 1e9:.1f} GB, "
                f"rule={rule}): {src}"
            )
        plan.artifacts.append(artifact)

    if n_catalog_only:
        # Not a silent cap: say what was left out and why.
        logger.info(
            f"{n_catalog_only} public runs are known only from a catalog index "
            "and no packet references them; copying the index, not the runs"
        )
        plan.catalog_only = n_catalog_only
    if n_container:
        logger.info(
            f"{n_container} referenced paths are container directories, not "
            "units; their children arrive through their own references"
        )
        plan.containers = n_container

    # A run directory and its parent job directory are both referenced;
    # dedupe so the job's top-level files are not copied per run.
    plan.artifacts = _dedupe_nested(plan.artifacts)
    return plan


def _is_catalog_only(src: str, carriers: set[str]) -> bool:
    """True when a public run is merely catalogued, not consumed.

    Two files in this store enumerate the whole corpus:
    ``indexes/official_public_index.csv`` (85,025 rows) and
    ``analysis/filter_inventory.json`` (the Stage-1 discovery pass, which
    includes the vision, image-generation and audio tracks). A mention in
    either means "this run exists", not "an analysis used it".

    Requiring a :data:`STRONG_CARRIERS` mention --- a packet manifest or a
    ``components/`` symlink, both written only when a comparison actually
    consumed the run --- is what keeps the package at ~919 official runs
    instead of following a catalog into ~491 GB, most of it HEIM image
    output that no text-benchmark analysis has any use for.
    """
    if not src.startswith(CATALOG_ONLY_ROOTS):
        return False
    return not (carriers & STRONG_CARRIERS)


def _classify_miss(path: Path) -> str:
    """Type an unresolvable reference rather than lumping them together.

    The public mirror is a partial rsync, so a file's absence here does
    not mean it was absent upstream --- that distinction decides whether
    a hole in the package is recoverable by re-fetching or not at all.
    """
    if str(path).startswith("/data/crfm-helm-public"):
        return "absent_local_mirror_may_exist_upstream"
    parent = path.parent
    if parent.exists():
        return "absent_sibling_dir_present"
    return "absent_parent_missing"


def _dedupe_nested(artifacts: list[Artifact]) -> list[Artifact]:
    """Drop artifacts wholly contained in another whole-copy artifact.

    Walks each candidate's ancestors against a set of whole-copy paths
    --- O(n * depth) --- rather than comparing every artifact against
    every other. The pairwise form is quadratic, and on this corpus
    (~10^5 candidate references) it does not finish.
    """
    whole = {a.src for a in artifacts if a.rule == RULE_WHOLE}
    kept: list[Artifact] = []
    n_nested = 0
    for artifact in artifacts:
        ancestors = Path(artifact.src).parents
        if any(str(ancestor) in whole for ancestor in ancestors):
            n_nested += 1
            continue
        kept.append(artifact)
    if n_nested:
        logger.info(f"{n_nested} artifacts nested under a whole-copy parent, skipped")
    return kept


def _measure_artifact(path: Path, rule: str) -> tuple[int, int]:
    n_files = 0
    n_bytes = 0
    for entry in _iter_artifact_files(path, rule):
        try:
            n_bytes += entry.stat().st_size
            n_files += 1
        except OSError:
            continue
    return n_files, n_bytes


def _iter_artifact_files(path: Path, rule: str):
    """The files one artifact contributes, under its retention rule."""
    if rule == RULE_FILE:
        yield path
        return
    if rule == RULE_JOB_TOPLEVEL:
        try:
            for entry in sorted(path.iterdir()):
                if entry.is_file() and not entry.is_symlink():
                    yield entry
        except OSError as exc:
            logger.warning(f"cannot list job dir {path}: {exc}")
        return
    if rule == RULE_EXPERIMENT:
        try:
            entries = sorted(path.iterdir())
        except OSError as exc:
            logger.warning(f"cannot list experiment dir {path}: {exc}")
            return
        for entry in entries:
            if entry.is_file() and not entry.is_symlink():
                yield entry
            elif entry.is_dir() and entry.name in EXPERIMENT_KEEP_DIRS:
                for nested in walk(entry):
                    if nested.is_file() and not nested.is_symlink():
                        yield nested
        return
    for entry in walk(path):
        if entry.is_file() and not entry.is_symlink():
            yield entry


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------


def write_plan(plan: PackagePlan, fpath: Path) -> None:
    """Dump the plan, largest artifact first, for review before copying.

    The ordering is the point: a packaging mistake shows up as one
    implausibly large unit at the top of this file, which is cheaper to
    notice here than after a multi-hour copy.
    """
    artifacts = sorted(plan.artifacts, key=lambda a: a.n_bytes, reverse=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "n_analyses": len(plan.analyses),
        "n_artifacts": len(plan.artifacts),
        "n_bytes": plan.n_bytes,
        "catalog_only_not_followed": plan.catalog_only,
        "containers_not_followed": plan.containers,
        "missing": [
            {"path": p, "reason": r, "n_refs": n} for p, r, n in sorted(plan.missing)
        ],
        "artifacts": [
            {"src": a.src, "rule": a.rule, "n_files": a.n_files,
             "n_bytes": a.n_bytes, "n_refs": a.n_refs, "referrers": a.referrers}
            for a in artifacts
        ],
    }
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def mirror_dest(src: str, package_dpath: Path) -> Path:
    """``/data/crfm-helm-audit/x`` -> ``<package>/root/data/crfm-helm-audit/x``.

    The absolute path is mirrored whole, minus its leading separator, so
    roots that do not share a common parent still land as siblings and
    every relative symlink between them keeps its depth.
    """
    rel = Path(src).as_posix().lstrip("/")
    return package_dpath / MIRROR_DIRNAME / rel


def execute_plan(
    plan: PackagePlan,
    package_dpath: Path,
    *,
    roots: tuple[str, ...] = DEFAULT_SOURCE_ROOTS,
    rewrite: bool = True,
    resume: bool = True,
) -> dict:
    """Copy, rewrite, verify. Returns the manifest dict."""
    package_dpath.mkdir(parents=True, exist_ok=True)
    drops = DropLog()
    copied = Counter()

    logger.info(f"copying {len(plan.analyses)} analyses")
    for record in plan.analyses:
        src = Path(record.path)
        if not src.exists():
            continue
        dest = mirror_dest(record.path, package_dpath)
        if src.is_file():
            # A loose analysis input (filter_inventory.json, the dataset
            # CSVs) is a unit in its own right, not a directory.
            copied["analysis_files"] += int(_copy_file(src, dest, resume=resume))
            continue
        copied["analysis_files"] += _copy_tree(
            src, dest, drops, resume=resume, file_filter=classify_analysis_file
        )

    logger.info(f"copying {len(plan.artifacts)} deduplicated artifacts")
    for artifact in plan.artifacts:
        src = Path(artifact.src)
        dest_root = mirror_dest(artifact.src, package_dpath)
        try:
            for entry in _iter_artifact_files(src, artifact.rule):
                rel = entry.relative_to(src) if src.is_dir() else Path(entry.name)
                dest = (dest_root / rel) if src.is_dir() else dest_root
                if _copy_file(entry, dest, resume=resume):
                    copied["artifact_files"] += 1
        except OSError as exc:
            artifact.status = f"error: {exc}"
            logger.error(f"failed copying {artifact.src}: {exc}")
        _record_artifact_drops(src, artifact.rule, drops)

    rewrites: list[dict] = []
    pre_hashes: dict[str, str] = {}
    if rewrite:
        rewrites, pre_hashes = _rewrite_paths(package_dpath, roots)

    problems = verify_package(package_dpath, roots, rewritten=rewrite)
    manifest = _write_sidecars(
        plan, package_dpath, drops, rewrites, pre_hashes, problems, copied, roots
    )
    return manifest


def _copy_tree(
    src: Path,
    dest: Path,
    drops: DropLog,
    *,
    resume: bool,
    file_filter,
) -> int:
    """Copy a directory, preserving symlinks verbatim, applying ``file_filter``."""
    n_copied = 0
    dest.mkdir(parents=True, exist_ok=True)
    for entry in walk(src):
        rel = entry.relative_to(src)
        if any(part in JUNK_NAMES for part in rel.parts):
            continue
        target = dest / rel
        if entry.is_symlink():
            _copy_symlink(entry, target)
            continue
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        decision = file_filter(entry)
        if not decision.keep:
            try:
                drops.record(entry, decision.reason, entry.stat().st_size)
            except OSError:
                drops.record(entry, decision.reason, 0)
            continue
        if _copy_file(entry, target, resume=resume):
            n_copied += 1
    return n_copied


def _copy_symlink(link: Path, dest: Path) -> None:
    """Recreate a symlink verbatim.

    Relative links are reproduced byte for byte: the mirror preserves the
    depth they were computed against, so rewriting them would break what
    copying them intact keeps working.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        target = os.readlink(link)
    except OSError as exc:
        logger.warning(f"cannot read symlink {link}: {exc}")
        return
    if dest.is_symlink() or dest.exists():
        try:
            if os.readlink(dest) == target:
                return
            dest.unlink()
        except OSError:
            return
    try:
        dest.symlink_to(target)
    except OSError as exc:
        logger.warning(f"cannot create symlink {dest}: {exc}")


def _copy_file(src: Path, dest: Path, *, resume: bool, retries: int = 40) -> bool:
    """Copy one file, skipping unchanged destinations and surviving EMFILE.

    This filesystem intermittently exhausts file descriptors under load;
    a packager that dies on the first ``OSError`` would never finish a
    27 GB copy. Retry the descriptor-exhaustion case and let everything
    else propagate.
    """
    try:
        stat = src.stat()
    except OSError as exc:
        logger.warning(f"cannot stat {src}: {exc}")
        return False
    if resume and dest.exists():
        try:
            if dest.stat().st_size == stat.st_size:
                return False
        except OSError:
            pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            shutil.copy2(src, dest)
            return True
        except OSError as exc:
            if exc.errno == 24:  # EMFILE
                time.sleep(0.25 * (attempt + 1))
                continue
            logger.warning(f"cannot copy {src}: {exc}")
            return False
    logger.error(f"gave up copying {src} after {retries} EMFILE retries")
    return False


def _record_artifact_drops(src: Path, rule: str, drops: DropLog) -> None:
    """Log what a retention rule excluded, so the package justifies itself."""
    if rule == RULE_JOB_TOPLEVEL:
        for name in sorted(JOB_SKIP_DIRS):
            skipped = src / name
            if skipped.is_dir():
                drops.record(skipped, "job_execution_state_not_read_by_analysis", -1)
    elif rule == RULE_EXPERIMENT:
        if (src / "helm").is_dir():
            drops.record(src / "helm", "job_dirs_packaged_via_own_references", -1)


# --------------------------------------------------------------------------
# path rewriting
# --------------------------------------------------------------------------


def _rewrite_paths(
    package_dpath: Path, roots: tuple[str, ...]
) -> tuple[list[dict], dict[str, str]]:
    """Repoint absolute source paths at the package, invertibly.

    Every substitution is ``/data/<root>`` -> ``<package>/data/<root>``,
    so ``rewrites.json`` plus the pre-rewrite hashes make the transform
    reversible and re-appliable if the package is later moved.
    """
    mirror = package_dpath / MIRROR_DIRNAME
    subs = [(root, str(mirror_dest(root, package_dpath))) for root in rewrite_roots(roots)]
    rewrites: list[dict] = []
    pre_hashes: dict[str, str] = {}

    logger.info(f"rewriting absolute paths under {mirror}")
    for entry in walk(mirror):
        if entry.is_symlink() or not entry.is_file():
            continue
        if entry.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        counts = {old: text.count(old) for old, _ in subs}
        if not any(counts.values()):
            continue
        rel = entry.relative_to(package_dpath).as_posix()
        pre_hashes[rel] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        for old, new in subs:
            if counts[old]:
                text = text.replace(old, new)
        try:
            entry.write_text(text, encoding="utf-8")
        except OSError as exc:
            logger.warning(f"cannot rewrite {entry}: {exc}")
            pre_hashes.pop(rel, None)
            continue
        rewrites.append(
            {
                "file": rel,
                "substitutions": {old: new for old, new in subs if counts[old]},
                "counts": {old: n for old, n in counts.items() if n},
                "sha256_after": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    logger.info(f"rewrote {len(rewrites)} files")
    return rewrites, pre_hashes


def repoint(package_dpath: Path) -> int:
    """Re-apply the recorded rewrites for a package that has moved.

    Run once after extracting the archive somewhere other than where it
    was built. Reads ``rewrites.json`` for the location the package was
    built at and swaps that prefix for where the package now is, in
    exactly the files that were rewritten before.
    """
    package_dpath = package_dpath.resolve()
    new_root = package_dpath
    rewrites_fpath = package_dpath / "rewrites.json"
    payload = json.loads(rewrites_fpath.read_text(encoding="utf-8"))
    old_root = payload["package_dpath"]
    if old_root == str(new_root):
        logger.info("package already points at this location")
        return 0
    n = 0
    for row in payload["rewrites"]:
        fpath = package_dpath / row["file"]
        if not fpath.exists():
            logger.warning(f"recorded rewrite target missing: {row['file']}")
            continue
        try:
            text = fpath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(f"cannot repoint {fpath}: {exc}")
            continue
        updated = text.replace(old_root, str(new_root))
        if updated != text:
            fpath.write_text(updated, encoding="utf-8")
            n += 1
    payload["package_dpath"] = str(new_root)
    rewrites_fpath.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    logger.info(f"repointed {n} files to {new_root}")
    return n


# --------------------------------------------------------------------------
# verification and sidecars
# --------------------------------------------------------------------------


def source_of(dest: Path, package_dpath: Path) -> Path:
    """Invert :func:`mirror_dest`: package path -> original absolute path.

    Deliberately does not call ``resolve()`` on ``dest``: that follows
    symlinks, and this is called *on* symlinks --- including broken ones,
    where resolving would hand back the missing target instead of the
    link itself.
    """
    rel = Path(os.path.abspath(dest)).relative_to(
        os.path.abspath(package_dpath / MIRROR_DIRNAME)
    )
    return Path("/") / rel


def verify_package(
    package_dpath: Path, roots: tuple[str, ...], *, rewritten: bool
) -> list[dict]:
    """Check the package stands on its own. Findings, not exceptions.

    Findings carry a severity because two different things present as a
    broken link. A link that resolved in the store and does not resolve
    here is a packager defect (``error``). A link that was *already*
    broken in the store is part of the record we are preserving
    (``info``) --- this corpus ships 38 of them, and refusing to package
    a store because it documents its own holes would be backwards.
    """
    problems: list[dict] = []
    mirror = package_dpath / MIRROR_DIRNAME
    package_root = package_dpath.resolve()
    n_links = 0
    for entry in walk(mirror):
        if not entry.is_symlink():
            continue
        n_links += 1
        resolved = (entry.parent / os.readlink(entry)).resolve(strict=False)
        if not resolved.exists():
            src_link = source_of(entry, package_dpath)
            preexisting = src_link.is_symlink() and not src_link.exists()
            problems.append(
                {
                    "kind": "preexisting_broken_symlink" if preexisting
                            else "broken_symlink",
                    "severity": "info" if preexisting else "error",
                    "path": entry.relative_to(package_dpath).as_posix(),
                }
            )
        elif package_root not in resolved.parents:
            problems.append(
                {
                    "kind": "symlink_escapes_package",
                    "severity": "error",
                    "path": entry.relative_to(package_dpath).as_posix(),
                    "target": str(resolved),
                }
            )
    n_broken = sum(1 for p in problems if p["kind"] == "preexisting_broken_symlink")
    logger.info(
        f"verified {n_links} symlinks "
        f"({n_broken} already broken in the source store)"
    )

    if rewritten:
        # After a rewrite, no *bare* source root should survive. A root
        # still appears inside every rewritten path -- the package prefix
        # is prepended, not substituted -- so blank the rewritten form
        # out first and look for what is left.
        #
        # Absolute paths from upstream HELM machines (/data/CLEAR,
        # /data/medhelm, /data/tasks_1-20_v1-2.tmp) are deliberately
        # untouched and never checked: they are recorded evidence about
        # where Stanford ran the job, not references into our filesystem.
        prefix = str(package_dpath / MIRROR_DIRNAME) + "/"
        for entry in walk(mirror):
            if entry.is_symlink() or not entry.is_file():
                continue
            if entry.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            residual = text.replace(prefix, "")
            for root in rewrite_roots(roots):
                if root in residual:
                    problems.append(
                        {
                            "kind": "unrewritten_source_root",
                            "severity": "error",
                            "path": entry.relative_to(package_dpath).as_posix(),
                            "root": root,
                            "n": residual.count(root),
                        }
                    )
                    break
    return problems


def _write_sidecars(
    plan: PackagePlan,
    package_dpath: Path,
    drops: DropLog,
    rewrites: list[dict],
    pre_hashes: dict[str, str],
    problems: list[dict],
    copied: Counter,
    roots: tuple[str, ...],
) -> dict:
    drops.write_tsv(package_dpath / "drops.tsv")

    missing_lines = ["path\treason\tn_refs"]
    missing_lines += [f"{p}\t{r}\t{n}" for p, r, n in sorted(plan.missing)]
    (package_dpath / "missing.tsv").write_text(
        "\n".join(missing_lines) + "\n", encoding="utf-8"
    )

    (package_dpath / "rewrites.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "package_dpath": str(package_dpath),
                "source_roots": list(roots),
                "mirror_dirname": MIRROR_DIRNAME,
                "rewrites": rewrites,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (package_dpath / "pre_rewrite_hashes.json").write_text(
        json.dumps(pre_hashes, indent=2, sort_keys=True), encoding="utf-8"
    )

    by_rule = Counter(a.rule for a in plan.artifacts)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "packaged_utc": datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "package_dpath": str(package_dpath),
        "source_roots": list(roots),
        "counts": {
            "analyses": len(plan.analyses),
            "distinct_artifacts": len(plan.artifacts),
            "total_references": plan.ref_table.total_refs,
            "distinct_referenced_paths": len(plan.ref_table),
            "dedup_ratio": round(
                plan.ref_table.total_refs / max(len(plan.ref_table), 1), 2
            ),
            "missing_references": len(plan.missing),
            "catalog_only_not_followed": plan.catalog_only,
            "containers_not_followed": plan.containers,
            "files_copied": dict(copied),
            "artifacts_by_rule": dict(by_rule),
            "files_rewritten": len(rewrites),
            "verification_errors": sum(
                1 for p in problems if p.get("severity") == "error"
            ),
            "verification_notes": sum(
                1 for p in problems if p.get("severity") != "error"
            ),
        },
        "bytes": {
            "analyses": sum(a.n_bytes for a in plan.analyses),
            "artifacts": sum(a.n_bytes for a in plan.artifacts),
            "dropped": drops.total_bytes,
        },
        "analyses": [
            {"id": r.id, "kind": r.kind, "rel_path": r.rel_path,
             "freshness": r.freshness, "n_packets": r.n_packets,
             "generated_utc": r.generated_utc, "status": r.status}
            for r in plan.analyses
        ],
        "artifacts": [
            {"src": a.src, "rule": a.rule, "n_refs": a.n_refs,
             "referrers": a.referrers, "n_files": a.n_files,
             "n_bytes": a.n_bytes, "status": a.status}
            for a in plan.artifacts
        ],
        "problems": problems,
    }
    (package_dpath / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    (package_dpath / "REPACK.md").write_text(_repack_readme(manifest), encoding="utf-8")
    _write_standalone_repoint(package_dpath)
    return manifest


#: A stdlib-only repoint script written *into* the package.
#:
#: The receiving machine should not need ``eval_audit`` installed merely
#: to make the package usable -- fixing absolute paths is the one step
#: that must work before anything else can, so it travels with the data.
_STANDALONE_REPOINT = '''#!/usr/bin/env python3
"""Repoint this package at wherever it now lives. Standard library only.

    python3 repoint.py            # use this script's own directory
    python3 repoint.py --check    # report what would change, write nothing

Re-applies the substitutions recorded in rewrites.json, swapping the
location the package was built at for the location it is now. Safe to
re-run: if the package has not moved it does nothing.
"""
import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_dpath", nargs="?", type=Path,
                        default=Path(__file__).resolve().parent)
    parser.add_argument("--check", action="store_true",
                        help="report only; write nothing")
    args = parser.parse_args()

    package = args.package_dpath.resolve()
    fpath = package / "rewrites.json"
    if not fpath.exists():
        print(f"no rewrites.json in {package}", file=sys.stderr)
        return 2

    payload = json.loads(fpath.read_text(encoding="utf-8"))
    old = payload["package_dpath"]
    new = str(package)
    if old == new:
        print(f"already pointing at {new}; nothing to do")
        return 0

    print(f"{old}\\n  -> {new}")
    changed = missing = 0
    for row in payload["rewrites"]:
        target = package / row["file"]
        if not target.exists():
            missing += 1
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"  cannot read {row['file']}: {exc}", file=sys.stderr)
            missing += 1
            continue
        updated = text.replace(old, new)
        if updated == text:
            continue
        changed += 1
        if not args.check:
            target.write_text(updated, encoding="utf-8")

    verb = "would update" if args.check else "updated"
    print(f"{verb} {changed} files" + (f"; {missing} missing" if missing else ""))
    if not args.check:
        payload["package_dpath"] = new
        fpath.write_text(json.dumps(payload, indent=2, sort_keys=True),
                         encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _write_standalone_repoint(package_dpath: Path) -> None:
    fpath = package_dpath / "repoint.py"
    fpath.write_text(_STANDALONE_REPOINT, encoding="utf-8")
    fpath.chmod(0o755)


def _repack_readme(manifest: dict) -> str:
    counts = manifest["counts"]
    return f"""# eval_audit analysis transfer package

Built {manifest['packaged_utc']} from `{manifest['package_dpath']}`.

Contains {counts['analyses']} analyses and {counts['distinct_artifacts']}
deduplicated artifacts ({counts['total_references']} references collapsed to
{counts['distinct_referenced_paths']} distinct paths, {counts['dedup_ratio']}x).

## What this is, and is not

This package carries what is needed to **redo the analysis** on another
machine. It is not a copy of the benchmark runs. Excluded by design, and
listed with sizes in `drops.tsv`:

- `prod_env/cache/*.sqlite` --- HELM request caches. Never read by the
  analysis.
- `benchmark_output/{{scenario_instances,scenarios}}/` --- the datasets
  HELM downloaded in order to execute. Never read by the analysis.
- `*.png` / `*.jpg` where the directory ships `redraw_plots.sh`.

Re-running the benchmarks from this package is *not* possible. Re-running
the analysis is.

## Using it after extraction

The tree under `root/` mirrors the original absolute layout, so the
relative symlinks inside every core-report packet resolve unchanged.

Absolute paths embedded in JSON, CSV and shell artifacts point at this
package's build location. If you extracted it somewhere else, repoint
them once --- this needs nothing installed beyond `python3`:

    python3 repoint.py            # from inside this directory
    python3 repoint.py --check    # show what would change, write nothing

It is idempotent, and safe to run again after moving the package again.
If you do have `eval_audit` installed, `eval-audit-package-analyses
--repoint <dir>` does the same thing.

`rewrites.json` records every substitution and `pre_rewrite_hashes.json`
the SHA-256 of each rewritten file as it was in the store, so the
transform is invertible and each file can be checked against its
original.

## Files

| file | what it holds |
|---|---|
| `MANIFEST.json` | analyses, artifacts, counts, verification findings |
| `rewrites.json` | every path substitution, and where |
| `pre_rewrite_hashes.json` | pre-rewrite SHA-256 per rewritten file |
| `drops.tsv` | every excluded file, reason, bytes |
| `missing.tsv` | references that did not resolve, typed |
| `repoint.py` | stdlib-only path fixer; no install needed |

`missing.tsv` reasons distinguish
`absent_local_mirror_may_exist_upstream` (the public HELM mirror here is
a partial rsync --- the file may well exist upstream) from
`absent_parent_missing` (gone).

Analysis code is public; install `eval_audit` from source and point it at
`root/data/crfm-helm-audit-store`.
"""
