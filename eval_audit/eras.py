"""Era-pinned HELM reproduction registry resolver.

The audit corpus is 59% pre-v0.5 (classic-track ``v0.2.4`` / ``v0.3.0`` runs).
Those runs cannot be replayed by the modern ``helm-runner`` image (it pins
HELM 0.5.x + Python 3.11 + modern deps, and magnet's from-spec CLI imports
v0.5+ module paths). Each pre-v0.5 *era* gets its own CPU-only Docker image
whose HELM harness is checked out at the era's release commit, with era Python
and era dep pins — freezing the measurement instrument at the era so deployment
is the only variable.

This module is the single reader of ``docker/eras.yaml`` on the host side. It
resolves the ``(public_track, suite_version)`` signal — the same path-derived
signal the official public index records — to an :class:`EraSpec`. Absence of a
match means the *modern* era: the existing image + magnet CLI, unchanged, which
this module represents as ``None`` (never an ``EraSpec``).

See docs/planning/era-pinned-helm-containers-plan.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from eval_audit.infra.paths import repo_root

#: Capability tag a from-spec era image advertises (matched by the kwdagger
#: bridge to select the era replay pipeline, and by the image's ``org.aiq.era``
#: label guard). The one capability that exists today.
ERA_SHIM_FROM_SPEC = "era-shim-from-spec"

#: Wildcard token in a ``matches`` predicate: the key does not constrain.
_WILDCARD = "*"


@dataclass(frozen=True)
class EraMatch:
    """One ``(public_track, suite_version)`` predicate. ``"*"`` = don't-care."""

    public_track: str = _WILDCARD
    suite_version: str = _WILDCARD

    def matches(self, *, public_track: str | None, suite_version: str | None) -> bool:
        return self._field_ok(self.public_track, public_track) and self._field_ok(
            self.suite_version, suite_version
        )

    @staticmethod
    def _field_ok(predicate: str, value: str | None) -> bool:
        if predicate == _WILDCARD:
            return True
        return value is not None and str(value) == predicate


@dataclass(frozen=True)
class EraSpec:
    """One era = one HELM release commit = one image = one measurement instrument."""

    key: str
    helm_git_ref: str
    python_version: str
    constraints: str
    helm_extras: str
    capability: str
    image_name: str
    matches: tuple[EraMatch, ...] = field(default_factory=tuple)

    def matches_run(self, *, public_track: str | None, suite_version: str | None) -> bool:
        return any(
            m.matches(public_track=public_track, suite_version=suite_version)
            for m in self.matches
        )


def default_eras_yaml_fpath() -> Path:
    """Canonical location of the era registry (``<repo>/docker/eras.yaml``)."""
    return repo_root() / "docker" / "eras.yaml"


def load_era_registry(fpath: Path | str | None = None) -> dict[str, EraSpec]:
    """Load ``docker/eras.yaml`` into ``{era_key: EraSpec}``.

    Raises ``ValueError`` on a malformed registry (this is checked-in config,
    so a schema error is a bug, not an expected runtime condition).
    """
    path = Path(fpath) if fpath is not None else default_eras_yaml_fpath()
    return _load_era_registry_cached(str(path))


@lru_cache(maxsize=None)
def _load_era_registry_cached(path_str: str) -> dict[str, EraSpec]:
    import kwutil

    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"era registry not found: {path}")
    data = kwutil.Yaml.load(path)
    if not isinstance(data, dict) or "eras" not in data:
        raise ValueError(f"era registry {path} must be a mapping with an 'eras' key")
    raw_eras = data["eras"]
    if not isinstance(raw_eras, dict):
        raise ValueError(f"'eras' in {path} must be a mapping of key -> era spec")

    registry: dict[str, EraSpec] = {}
    for key, raw in raw_eras.items():
        registry[str(key)] = _parse_era_spec(str(key), raw, path)
    return registry


def _parse_era_spec(key: str, raw: Any, path: Path) -> EraSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"era {key!r} in {path} must be a mapping")
    # All fields explicit-required (B3): helm_extras/capability previously
    # defaulted here AND in docker/read_eras.py — two hardcoded copies of the
    # same defaults that could silently drift. The registry is checked-in
    # config; requiring the fields there keeps a single source of truth.
    required = (
        "helm_git_ref",
        "python_version",
        "constraints",
        "image_name",
        "helm_extras",
        "capability",
    )
    missing = [k for k in required if not raw.get(k)]
    if missing:
        raise ValueError(f"era {key!r} in {path} missing required keys: {missing}")

    raw_matches = raw.get("matches") or []
    if not isinstance(raw_matches, list):
        raise ValueError(f"era {key!r} 'matches' in {path} must be a list")
    matches = tuple(
        EraMatch(
            public_track=str(m.get("public_track", _WILDCARD)),
            suite_version=str(m.get("suite_version", _WILDCARD)),
        )
        for m in raw_matches
        if isinstance(m, dict)
    )
    return EraSpec(
        key=key,
        helm_git_ref=str(raw["helm_git_ref"]),
        python_version=str(raw["python_version"]),
        constraints=str(raw["constraints"]),
        helm_extras=str(raw["helm_extras"]),
        capability=str(raw["capability"]),
        image_name=str(raw["image_name"]),
        matches=matches,
    )


def resolve_era(
    public_track: str | None,
    suite_version: str | None,
    *,
    registry: dict[str, EraSpec] | None = None,
) -> EraSpec | None:
    """Resolve a ``(public_track, suite_version)`` signal to an era.

    Returns the matching :class:`EraSpec`, or ``None`` for the *modern* era
    (no registered match — the existing image + magnet CLI, unchanged).

    Raises ``ValueError`` if more than one era claims the signal (an
    ambiguous registry is a config bug that would silently pick an arbitrary
    instrument), or if ``public_track`` is undecidable (``None``) while
    ``suite_version`` alone names a registered era (Finding 9 — a track-rooted
    mirror that would otherwise silently resolve to the *modern* image and run a
    pre-v0.5 spec under the 0.5.x harness).
    """
    reg = registry if registry is not None else load_era_registry()
    hits = [
        era
        for era in reg.values()
        if era.matches_run(public_track=public_track, suite_version=suite_version)
    ]
    if len(hits) > 1:
        keys = ", ".join(sorted(e.key for e in hits))
        raise ValueError(
            f"ambiguous era registry: ({public_track!r}, {suite_version!r}) "
            f"matched multiple eras: {keys}"
        )
    if hits:
        return hits[0]
    # Finding 9: no full match. If the track was undecidable (None) but the
    # suite_version alone names a registered era, this is almost certainly a
    # track-rooted mirror whose path lacks the public_track component. Silently
    # returning modern would run a pre-v0.5 spec under the modern image with the
    # era<->image guard none the wiser (manifest era stays None). Fail loud.
    if public_track is None and suite_version is not None:
        suite_only = sorted(
            era.key
            for era in reg.values()
            if any(m.suite_version == suite_version for m in era.matches)
        )
        if suite_only:
            raise ValueError(
                f"cannot derive public_track (got None) but suite_version "
                f"{suite_version!r} matches era(s) {', '.join(suite_only)}. This is "
                "likely a track-rooted mirror whose --precomputed-root is the track "
                "dir itself; pass --era <key> explicitly, or point --precomputed-root "
                "one level up so the <track>/benchmark_output/... component is in the "
                "path."
            )
    return None


def parse_public_signal_from_run_dir(run_dir: Path | str) -> tuple[str | None, str | None]:
    """Derive ``(public_track, suite_version)`` from an official HELM run dir path.

    Same path convention the official public index and ``compare_batch`` use:
    ``<...>/<public_track>/benchmark_output/runs/<suite_version>/<run_leaf>``.
    ``public_track`` is the path component immediately *before*
    ``benchmark_output``; ``suite_version`` is the component two past it (the
    directory under ``runs/``). Either may be ``None`` if the path does not
    follow the convention.
    """
    parts = list(Path(run_dir).parts)
    try:
        idx = parts.index("benchmark_output")
    except ValueError:
        return None, None
    public_track = parts[idx - 1] if idx >= 1 else None
    # runs/<suite_version> => suite_version is two components past benchmark_output.
    suite_version = parts[idx + 2] if (idx + 2) < len(parts) else None
    return public_track, suite_version


def era_for_run_dir(
    run_dir: Path | str,
    *,
    registry: dict[str, EraSpec] | None = None,
) -> EraSpec | None:
    """Resolve the era for a single official run dir path (``None`` = modern)."""
    public_track, suite_version = parse_public_signal_from_run_dir(run_dir)
    return resolve_era(public_track, suite_version, registry=registry)


def resolve_era_for_sources(
    precomputed_root: Path | str,
    sources: list[dict[str, Any]],
    *,
    registry: dict[str, EraSpec] | None = None,
) -> EraSpec | None:
    """Resolve the single era shared by a set of exact-path replay sources.

    Each source carries a ``rel_path`` (relative to ``precomputed_root``)
    naming one official run dir. All sources in one manifest MUST resolve to
    the same era — one manifest = one era = one image = one measurement
    instrument. A mixed-era set is a hard error (raised here) rather than a
    silent pick.

    Returns the shared :class:`EraSpec`, or ``None`` when every source resolves
    to the modern era.
    """
    reg = registry if registry is not None else load_era_registry()
    root = Path(precomputed_root)
    seen: dict[str | None, EraSpec | None] = {}
    for source in sources:
        rel_path = source.get("rel_path")
        if not rel_path:
            # Non-exact-path source (run-entry label only): cannot resolve an
            # era from a path. Treat as modern; mixing is caught below.
            seen.setdefault(None, None)
            continue
        era = era_for_run_dir(root / str(rel_path), registry=reg)
        seen[era.key if era else None] = era

    distinct_keys = set(seen.keys())
    if len(distinct_keys) > 1:
        pretty = ", ".join(sorted(k if k is not None else "modern" for k in distinct_keys))
        raise ValueError(
            "mixed-era run_spec sources are not allowed in one manifest "
            f"(one manifest = one era = one image); resolved eras: {pretty}"
        )
    (only_key,) = distinct_keys if distinct_keys else (None,)
    return seen.get(only_key)
