"""Resolve and record container-image provenance for containerized HELM runs.

When a manifest opts into containerized execution (``container_image`` set), the
scheduler resolves that tag/ref to an immutable ``sha256`` digest **once** and
pins every kwdagger node to ``<repo>@sha256:<digest>``. This module provides:

* :func:`resolve_image_digest` — tag → digest resolution (pulls if needed),
  returning a structured :class:`ResolvedImage` plus any reproducibility
  warnings (e.g. a local-only image with no registry digest).
* :func:`write_container_provenance` — persist a provenance record to disk.

Why pin at schedule time: a single experiment then provably uses one known
image, and the recorded digest is auditable if the underlying tag is ever
re-pushed. See ``docs/container-execution.md``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class ResolvedImage:
    """The outcome of resolving a requested image reference to a run ref."""

    requested: str
    run_ref: str
    """The reference to hand to ``docker run`` — an immutable digest ref when
    one is available, otherwise the requested tag (with a warning)."""
    digest: str | None
    digest_kind: str  # "already_pinned" | "repo_digest" | "image_id" | "unresolved"
    pinned: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "run_ref": self.run_ref,
            "digest": self.digest,
            "digest_kind": self.digest_kind,
            "pinned": self.pinned,
            "warnings": list(self.warnings),
        }


def _runtime_bin(runtime: str) -> str:
    if shutil.which(runtime) is None:
        raise RuntimeError(
            f"container_runtime {runtime!r} not found on PATH; cannot resolve "
            "the container image digest at schedule time."
        )
    return runtime


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def runtime_version(runtime: str = "docker") -> str | None:
    """Best-effort version string of the container runtime, for the record."""
    if shutil.which(runtime) is None:
        return None
    proc = _run([runtime, "version", "--format", "{{.Server.Version}}"])
    out = proc.stdout.strip()
    if proc.returncode == 0 and out:
        return out
    proc = _run([runtime, "--version"])
    return proc.stdout.strip() or None


def _repo_of(reference: str) -> str:
    """The repository portion of an image reference (drop tag/digest)."""
    ref = reference.split("@", 1)[0]
    # A ':' after the last '/' is a tag (registry host ports stay before '/').
    head, sep, tail = ref.rpartition(":")
    if sep and "/" not in tail:
        return head
    return ref


def resolve_image_digest(image: str, runtime: str = "docker") -> ResolvedImage:
    """Resolve ``image`` to an immutable digest reference when possible.

    Strategy:
      1. If already pinned (``...@sha256:...``), return it unchanged.
      2. Otherwise pull (best effort), then inspect ``RepoDigests`` for a
         ``<repo>@sha256:...`` ref matching the requested repo — that is
         portable and used as the run ref.
      3. Fall back to the local image ``.Id`` for provenance only, leaving the
         run ref as the requested tag and emitting a reproducibility warning.
    """
    image = image.strip()
    if _DIGEST_RE.search(image):
        digest = image.split("@", 1)[1]
        return ResolvedImage(
            requested=image,
            run_ref=image,
            digest=digest,
            digest_kind="already_pinned",
            pinned=True,
        )

    bin_ = _runtime_bin(runtime)
    warnings: list[str] = []

    pull = _run([bin_, "pull", image])
    if pull.returncode != 0:
        warnings.append(
            f"`{runtime} pull {image}` failed (using any local copy): "
            f"{pull.stderr.strip().splitlines()[-1] if pull.stderr.strip() else 'unknown error'}"
        )

    inspect = _run([bin_, "image", "inspect", image, "--format", "{{json .RepoDigests}}"])
    if inspect.returncode != 0:
        raise RuntimeError(
            f"`{runtime} image inspect {image}` failed; image is not available "
            f"locally and could not be pulled.\n{inspect.stderr.strip()}"
        )
    try:
        repo_digests = json.loads(inspect.stdout.strip() or "[]") or []
    except json.JSONDecodeError:
        repo_digests = []

    requested_repo = _repo_of(image)
    chosen = None
    for rd in repo_digests:
        if _repo_of(rd) == requested_repo:
            chosen = rd
            break
    # P2: do NOT borrow a foreign repository's RepoDigest when the requested
    # repo has none — that digest@ref names a different repository, so pinning
    # to it is silently wrong. Fall through to the content-addressed image-id
    # branch instead (and warn).
    if chosen is None and repo_digests:
        warnings.append(
            f"{image!r} has no RepoDigest for its own repository "
            f"({requested_repo!r}); available digests belong to other repos "
            f"({[_repo_of(rd) for rd in repo_digests]}). Not borrowing a foreign "
            "digest — pinning to the local image id instead."
        )

    if chosen:
        digest = chosen.split("@", 1)[1]
        return ResolvedImage(
            requested=image,
            run_ref=chosen,
            digest=digest,
            digest_kind="repo_digest",
            pinned=True,
            warnings=warnings,
        )

    # Local-only image: no registry digest. Pin to the content-addressed image
    # id so a rebuild under the same tag changes the run_ref (and thus kwdagger
    # algo identity), forcing a recompute rather than reusing stale results (P2).
    # ``docker run <image_id>`` runs exactly that image.
    id_proc = _run([bin_, "image", "inspect", image, "--format", "{{.Id}}"])
    image_id = id_proc.stdout.strip() or None
    warnings.append(
        f"{image!r} has no registry digest (not pushed?). Running by local image "
        "id — this is NOT reproducible across machines. Push the image and "
        "reference it by digest for an auditable run."
    )
    return ResolvedImage(
        requested=image,
        run_ref=image_id or image,
        digest=image_id,
        digest_kind="image_id" if image_id else "unresolved",
        pinned=False,
        warnings=warnings,
    )


def image_label(
    image: str, key: str, runtime: str = "docker", *, pull_if_missing: bool = False
) -> str | None:
    """Return the value of OCI label ``key`` on ``image``, or ``None`` if absent.

    ``None`` means the image is inspectable but the label is genuinely absent —
    a real signal the caller can act on (e.g. "modern image, no era label").

    An image that cannot be inspected is NOT "label absent": conflating the two
    false-fails the era<->image guard for a digest-pinned era image that
    :func:`resolve_image_digest` short-circuited without pulling (it would read
    ``None`` and report a bogus "carries org.aiq.era=None" mismatch). So when
    ``pull_if_missing`` is set and the first inspect fails, attempt a best-effort
    pull and re-inspect; if the image still cannot be inspected, raise
    ``RuntimeError`` with an actionable "image not present" message. With
    ``pull_if_missing`` unset the old best-effort contract holds (inspect failure
    → ``None``).
    """
    bin_ = _runtime_bin(runtime)
    fmt = f"{{{{index .Config.Labels {key!r}}}}}"

    def _inspect():
        return _run([bin_, "image", "inspect", image, "--format", fmt])

    proc = _inspect()
    if proc.returncode != 0 and pull_if_missing:
        _run([bin_, "pull", image])  # best effort; the re-inspect is the decider
        proc = _inspect()
    if proc.returncode != 0:
        if pull_if_missing:
            raise RuntimeError(
                f"cannot inspect image {image!r} to read label {key!r}: it is not "
                "present locally and could not be pulled. Build or pull the image "
                f"before scheduling.\n{proc.stderr.strip()}"
            )
        return None
    value = proc.stdout.strip()
    # Docker prints "<no value>" for a missing label key.
    if not value or value == "<no value>":
        return None
    return value


def write_container_provenance(dpath: str | Path, record: dict[str, Any]) -> Path:
    """Write ``container_provenance.json`` into ``dpath`` and return its path."""
    dpath = Path(dpath)
    dpath.mkdir(parents=True, exist_ok=True)
    fpath = dpath / "container_provenance.json"
    fpath.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return fpath
