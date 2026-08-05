from __future__ import annotations

from pathlib import Path
from typing import Any

from eval_audit.infra.yaml_io import load_manifest
from eval_audit.integrations.kwdagger_bridge import (
    kwdagger_schedule_argv,
    kwdagger_schedule_command_text,
    prepare_schedule_request,
    run_kwdagger_schedule,
)
from eval_audit.workflows.attempt_collision import (
    report_attempt_collisions,
    scan_experiment_attempts,
)


def run_from_manifest(
    manifest_fpath: str | Path,
    *,
    run: bool = False,
    root_dpath: str | Path | None = None,
    queue_name: str | None = None,
    devices: str | None = None,
    tmux_workers: int | None = None,
    backend: str | None = None,
    container_image: str | None = None,
    lease: bool = False,
    lease_ttl: str | None = None,
    lease_timeout: str | None = None,
    lease_catalog: str | None = None,
    lease_queue: bool = True,
    strict_attempts: bool = False,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_fpath)
    request = prepare_schedule_request(
        manifest_fpath,
        run=run,
        root_dpath=root_dpath,
        queue_name=queue_name,
        devices=devices,
        tmux_workers=tmux_workers,
        backend=backend,
        container_image=container_image,
        lease=lease,
        lease_ttl=lease_ttl,
        lease_timeout=lease_timeout,
        lease_catalog=lease_catalog,
        lease_queue=lease_queue,
    )
    info: dict[str, Any] = {
        "experiment_name": str(manifest["experiment_name"]),
        "manifest_fpath": str(request.manifest_fpath),
        "mode": "execute" if request.runtime.run else "preview",
        "result_dpath": str(request.runtime.root_dpath),
        "queue_name": request.runtime.queue_name,
        "backend": request.runtime.backend,
        "devices": request.runtime.devices,
        "tmux_workers": request.runtime.tmux_workers,
        "argv": kwdagger_schedule_argv(request),
        "command": kwdagger_schedule_command_text(request),
    }
    if request.resolved_image is not None:
        info["container_image"] = request.resolved_image.to_dict()
    if request.runtime.run:
        # Snapshot before/after rather than predicting: whether a run entry
        # gains a *new* attempt depends on kwdagger's skip-vs-recompute
        # decision, so a pre-flight guess would fire on every plain resume.
        # See eval_audit/workflows/attempt_collision.py.
        experiment_root = request.runtime.root_dpath
        before = scan_experiment_attempts(experiment_root)
        proc = run_kwdagger_schedule(request)
        info["returncode"] = proc.returncode
        info["attempts"] = report_attempt_collisions(
            info["experiment_name"],
            before,
            scan_experiment_attempts(experiment_root),
            strict=strict_attempts,
        )
    return info
