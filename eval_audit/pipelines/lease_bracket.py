r"""Per-run infer-stack GPU-lease bracket — the orthogonal axis to containerization.

Every HELM run is containerized (the docker pipeline pins the software
environment); leasing is the *separate* axis layered on top. A run brackets
itself with an infer-stack lease — ``acquire`` (as a cmd_queue *gating* setup)
before the run and ``release`` (as an always-run EXIT/INT/TERM teardown) after —
so kwdagger fans many runs out and infer-stack's admission queue serializes the
ones that can't co-host.

The bracket is plain shell (``infer-stack acquire/release --env-file
<node>/lease.env``) and renders only when the manifest names a lease endpoint.
That orthogonality is what lets one containerized node serve both cases:

* a *served* run (vLLM behind LiteLLM) names a lease endpoint -> bracket renders,
  the HELM client is an HTTP caller with ``container_gpus: none``;
* an *in-process* run (HELM loads the model itself from HuggingFace) names no
  lease endpoint -> bracket is ``None``, the client gets a real GPU.

The lease acquires the *model server's* GPU; the container decides where the
*HELM client* process runs. The two never need to be coupled.

:class:`LeaseBracketMixin` is mixed into
:class:`eval_audit.pipelines.helm_docker_pipeline.MaterializeHelmRunDockerNode`.
A node that mixes it in must (1) declare the lease knobs as identity-neutral perf
params (spread :data:`LEASE_PERF_PARAMS`) so they land in ``final_config`` where
the setup/teardown render from them, and (2) strip :data:`LEASE_KEYS` from the
inner materialize CLI it renders (they configure the bracket, not the work) —
:func:`render_magnet_command` does this via its ``exclude`` argument.
"""

from __future__ import annotations

import json
import shlex
from typing import Any

# final_config keys that configure the per-run infer-stack GPU lease (the job
# setup/teardown bracket). They shape the rendered ``setup``/``teardown`` shell
# and must be stripped from the inner materialize CLI. ``lease_endpoint`` is the
# scalar single-model form; ``lease_endpoints`` is a JSON map
# ``{model_deployment_name: catalog_endpoint}`` for multi-model manifests,
# resolved per run-entry against the entry's ``model_deployment=``.
LEASE_KEYS = frozenset(
    {
        "lease_endpoint",
        "lease_endpoints",
        "lease_ttl",
        "lease_catalog",
        "lease_queue",
        "lease_snapshot",
    }
)

# Identity-neutral perf params a HELM-run node spreads to accept the lease knobs.
# They describe *how* the model is served, not *what* is computed, so they are
# perf (recorded, identity-neutral) rather than algo params.
LEASE_PERF_PARAMS: dict[str, Any] = {key: None for key in LEASE_KEYS}

# Default soft TTL for a pipeline lease when a manifest does not pin one. Must
# exceed worst-case (model cold-load + the run) so a hard-killed job's leaked
# lease is reclaimed by the admission queue's sweep / ``infer-stack gc`` rather
# than expiring mid-run (design §8). Per-endpoint overrides flow via the
# manifest's ``lease_ttl``.
_DEFAULT_LEASE_TTL = "4h"

# The fixed gateway path each lease.env is written under, relative to the node's
# own output dir (``out_dpath``). setup writes it (``acquire --env-file``);
# teardown reads it (``release --env-file``). Per-node so concurrent jobs never
# share a lease handle.
_LEASE_ENV_BASENAME = "lease.env"


def _coerce_map(value: Any) -> dict[str, str]:
    """Coerce a matrix value (dict, or JSON string) into a ``{str: str}`` map."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError(f"lease_endpoints must be a JSON object, got {value!r}")
        return {str(k): str(v) for k, v in parsed.items()}
    raise ValueError(f"cannot coerce {value!r} into a lease-endpoints map")


def _parse_model_deployment(run_entry: Any) -> str | None:
    """Extract the ``model_deployment=<name>`` token from a HELM run-entry.

    HELM run-entries are ``scenario:key=value,key=value,...`` — the
    ``model_deployment`` knob names the model_deployments.yaml entry (and, via
    the preset profile, the catalog endpoint). Returns ``None`` if absent.
    """
    if not run_entry:
        return None
    for token in str(run_entry).split(","):
        token = token.strip()
        if token.startswith("model_deployment="):
            return token.split("=", 1)[1].strip()
    return None


def _resolve_lease_endpoint(cfg: dict[str, Any]) -> str | None:
    """Resolve the catalog endpoint this run must lease, or ``None`` if leasing
    is not requested for it.

    Scalar ``lease_endpoint`` wins for single-model manifests. A
    ``lease_endpoints`` map (multi-model manifests) is resolved against the
    run-entry's ``model_deployment=`` token; a single-entry map degenerates to
    its sole value. A non-empty map that cannot be resolved is a hard error (the
    C-3 name-chain hazard — better to fail the schedule than route a run at a
    model that was never leased).
    """
    endpoints_map = _coerce_map(cfg.get("lease_endpoints"))
    if endpoints_map:
        deployment = _parse_model_deployment(cfg.get("run_entry"))
        if deployment is not None and deployment in endpoints_map:
            return endpoints_map[deployment]
        if deployment is None and len(endpoints_map) == 1:
            return next(iter(endpoints_map.values()))
        raise ValueError(
            "lease_endpoints map cannot be resolved for run_entry "
            f"{cfg.get('run_entry')!r}: model_deployment={deployment!r} not in "
            f"{sorted(endpoints_map)}. Pin model_deployment=<name> in the "
            "run-entry so its lease endpoint is unambiguous."
        )
    endpoint = cfg.get("lease_endpoint")
    return str(endpoint) if endpoint else None


def render_lease_setup(cfg: dict[str, Any]) -> str | None:
    """Render the infer-stack ``acquire`` setup for one HELM-run node.

    The setup is a *gating precondition* (cmd_queue runs it before the command
    and skips the command if it fails). It queues-and-waits for the model's GPU
    (``--queue``), writes a per-node lease handle, and — best-effort — snapshots
    the concurrency context (co-held lease ``demand``) the determinism study
    consumes (design §7). Returns ``None`` when no lease is requested.
    """
    endpoint = _resolve_lease_endpoint(cfg)
    if not endpoint:
        return None
    out_dpath = str(cfg["out_dpath"])
    q = shlex.quote
    env_file = q(f"{out_dpath}/{_LEASE_ENV_BASENAME}")
    ttl = str(cfg.get("lease_ttl") or _DEFAULT_LEASE_TTL)
    queue = cfg.get("lease_queue")
    queue = True if queue is None else bool(queue)

    acquire = ["infer-stack", "acquire", q(endpoint), "--ttl", q(ttl), "--yes"]
    if queue:
        acquire.append("--queue")
    acquire += ["--env-file", env_file]
    catalog = cfg.get("lease_catalog")
    if catalog:
        acquire += ["--catalog", q(str(catalog))]

    # Ensure the node dir exists before acquire writes lease.env into it (the
    # inner materialize CLI creates out_dpath, but it has not run yet at setup
    # time). Chained with && so a failed acquire still gates the command.
    parts = [f"mkdir -p {q(out_dpath)}", " ".join(acquire)]

    snapshot = cfg.get("lease_snapshot")
    snapshot = True if snapshot is None else bool(snapshot)
    if snapshot:
        snap_path = q(f"{out_dpath}/concurrency_snapshot.json")
        # Best-effort: a snapshot hiccup must never fail the (gating) setup. The
        # ``|| true`` is scoped INSIDE the brace group so it swallows only the
        # snapshot's status — the preceding ``&& acquire`` still gates, so a
        # failed acquire keeps the whole chain false (PREAMBLE_OK=0) and the
        # HELM command is correctly skipped. Records co-held demand at run start
        # for the agreement-vs-concurrency analysis (design §7).
        parts.append(f"{{ infer-stack leases --json > {snap_path} 2>/dev/null || true ; }}")
    return " && ".join(parts)


def render_lease_teardown(cfg: dict[str, Any]) -> str | None:
    """Render the infer-stack ``release`` teardown for one HELM-run node.

    cmd_queue arms this as an always-run trap (EXIT/INT/TERM) *only* if setup
    succeeded, so it releases exactly the lease setup acquired — on success,
    failure, and SIGTERM. Returns ``None`` when no lease is requested.
    """
    endpoint = _resolve_lease_endpoint(cfg)
    if not endpoint:
        return None
    out_dpath = str(cfg["out_dpath"])
    env_file = shlex.quote(f"{out_dpath}/{_LEASE_ENV_BASENAME}")
    return f"infer-stack release --env-file {env_file}"


def render_magnet_command(
    executable: str, cfg: dict[str, Any], *, exclude: frozenset[str] = frozenset()
) -> str:
    """Render the magnet ``materialize_helm_run`` CLI from a config dict.

    Mirrors :meth:`magnet…MaterializeHelmRunNode.command` exactly (skip
    ``None``/empty-list values; YAML-encode dicts) so a leased node's inner
    command is byte-for-byte what the unmodified bare pipeline would emit — minus
    the ``exclude`` keys (container/lease knobs that configure the wrapper or the
    lease bracket, never the work).
    """
    parts: list[str] = []
    for key, value in cfg.items():
        if key in exclude:
            continue
        if value is None:
            continue
        if isinstance(value, list) and len(value) == 0:
            continue
        if isinstance(value, dict):
            from kwutil.util_yaml import Yaml

            value_text = shlex.quote(Yaml.dumps(value))
            if "\n" in value_text and value_text[0] == "'":
                value_text = "'\n" + value_text[1:]
        else:
            value_text = shlex.quote(str(value))
        parts.append(f"    --{key}={value_text} \\")
    argstr = "\n".join(parts).lstrip().rstrip("\\")
    if argstr:
        return executable + " \\\n    " + argstr
    return executable


class LeaseBracketMixin:
    """Add the infer-stack lease bracket to a HELM-run ``ProcessNode``.

    Exposes ``setup``/``teardown`` as ``final_config``-derived properties so each
    matrix-point brackets its own endpoint + per-node lease handle, and ``None``
    when the manifest requests no lease (the bare, pre-leasing behavior). The
    mixin is transport-neutral — the containerized and bare host-venv nodes both
    mix it in unchanged.

    Mix it in *before* the magnet node so the properties shadow the base's
    ``setup``/``teardown`` class attributes::

        class MaterializeHelmRun*Node(LeaseBracketMixin, MaterializeHelmRunNode):
            perf_params = {**MaterializeHelmRunNode.perf_params, **LEASE_PERF_PARAMS}
    """

    @property
    def setup(self) -> str | None:
        return render_lease_setup(dict(self.final_config))

    @setup.setter
    def setup(self, value: Any) -> None:
        # ``ProcessNode.__init__`` assigns ``setup`` (default ``None``) via
        # _classvar_init's setattr; we derive it dynamically from final_config
        # instead, so absorb and ignore that one construction-time assignment.
        pass

    @property
    def teardown(self) -> str | None:
        return render_lease_teardown(dict(self.final_config))

    @teardown.setter
    def teardown(self, value: Any) -> None:
        # See ``setup.setter``: absorb _classvar_init's construction assignment.
        pass
