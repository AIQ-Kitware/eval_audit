"""P2 regressions for image-digest resolution in docker_provenance.

* A foreign repository's RepoDigest must NOT be borrowed when the requested
  repo has none — fall through to the content-addressed local image id.
* An unpinned local image resolves its run_ref to the image id so a rebuild
  under the same tag changes kwdagger algo identity (forcing recompute).
"""
from __future__ import annotations

import json
import subprocess

import pytest

from eval_audit.integrations import docker_provenance


def _fake_run_factory(repo_digests: list[str], image_id: str):
    def _fake_run(cmd, *args, **kwargs):
        joined = " ".join(cmd)
        if "RepoDigests" in joined:
            out = json.dumps(repo_digests)
        elif "{{.Id}}" in joined:
            out = image_id
        else:  # pull
            out = ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    return _fake_run


IMAGE_ID = "sha256:" + "b" * 64


def test_foreign_repo_digest_is_not_borrowed(monkeypatch):
    # Requested repo has no digest; the only available digest belongs to a
    # different repository. Must NOT pin to it.
    monkeypatch.setattr(
        docker_provenance,
        "_run",
        _fake_run_factory(["other/repo@sha256:" + "c" * 64], IMAGE_ID),
    )
    resolved = docker_provenance.resolve_image_digest("myrepo/img:latest")
    assert resolved.run_ref == IMAGE_ID
    assert resolved.digest_kind == "image_id"
    assert resolved.pinned is False
    assert any("Not borrowing a foreign digest" in w for w in resolved.warnings)


def test_unpinned_local_image_uses_image_id_as_run_ref(monkeypatch):
    monkeypatch.setattr(
        docker_provenance, "_run", _fake_run_factory([], IMAGE_ID)
    )
    resolved = docker_provenance.resolve_image_digest("myrepo/img:latest")
    # run_ref is the content-addressed image id (not the tag) so a rebuild
    # under the same tag changes algo identity.
    assert resolved.run_ref == IMAGE_ID
    assert resolved.digest_kind == "image_id"


def test_matching_repo_digest_is_pinned(monkeypatch):
    match = "myrepo/img@sha256:" + "d" * 64
    monkeypatch.setattr(
        docker_provenance, "_run", _fake_run_factory([match], IMAGE_ID)
    )
    resolved = docker_provenance.resolve_image_digest("myrepo/img:latest")
    assert resolved.run_ref == match
    assert resolved.digest_kind == "repo_digest"
    assert resolved.pinned is True


def test_image_label_uses_double_quoted_go_template(monkeypatch):
    # Regression: the --format template must double-quote the label key. Python's
    # {key!r} emits single quotes, which docker's Go template parser rejects with
    # "malformed character constant". _run is mocked in the suite, so guard the
    # actual template string image_label builds rather than trusting a mock.
    captured = {}

    def _fake_run(cmd, *args, **kwargs):
        if "--format" in cmd:
            captured["fmt"] = cmd[cmd.index("--format") + 1]
        return subprocess.CompletedProcess(cmd, 0, stdout="helm-v0.2.4", stderr="")

    monkeypatch.setattr(docker_provenance, "_run", _fake_run)
    value = docker_provenance.image_label("img@sha256:" + "e" * 64, "org.aiq.era")
    assert value == "helm-v0.2.4"
    fmt = captured["fmt"]
    assert fmt == '{{index .Config.Labels "org.aiq.era"}}', fmt
    assert "'org.aiq.era'" not in fmt  # single quotes => Go character-constant error
