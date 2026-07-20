r"""kwdagger pipeline for annotation-only rejudge jobs (open-judge-plan Commit 11).

One node per (judge, benchmark, replicate). Each job brackets itself with an
infer-stack lease — the SAME :class:`LeaseBracketMixin` the containerized HELM
node uses — so kwdagger fans the matrix out and infer-stack's admission queue
serializes whatever cannot co-host. Four TP1 judge arms co-host across four
cards and run concurrently; the serial bash driver
(``reproduce/open_judge_gpt_oss/50_overnight_run.sh``) could not express that.

Why this is NOT the containerized node
--------------------------------------
A rejudge performs no candidate inference and loads no model in-process: it
calls HELM's ``AnnotationExecutor`` against a gateway over HTTP. There is no
GPU for the job process and no HELM model environment to pin, so the Docker
wrapper would buy nothing. Leasing and containerization are orthogonal axes
(see :mod:`eval_audit.pipelines.lease_bracket`); this node takes the lease axis
only. The lease acquires the *judge server's* GPU; this process is an HTTP
caller.

Output contract
---------------
The rejudge CLI writes its artifact into a CONTENT-ADDRESSED store at
``<out_root>/<attempt_hash>/`` — shared across jobs, and what the analysis CLI
scans — not into the node directory. So the node's own ``DONE`` is a thin job
receipt touched after the CLI succeeds, which is what kwdagger's completion /
``skip_existing`` check reads. The two gates are independent and both safe: the
CLI is itself idempotent (it returns a cache hit when the store artifact's DONE
already exists), so a node re-run costs a process start, not a rejudge.
"""

from __future__ import annotations

import shlex
from typing import Any

import kwdagger

from eval_audit.pipelines.lease_bracket import (
    LEASE_KEYS as _LEASE_KEYS,
    LEASE_PERF_PARAMS,
    LeaseBracketMixin,
)

#: final_config keys consumed by the node itself, never forwarded to the CLI.
#: ``judge_spec_hash`` is job IDENTITY only — it is derived from judge_json's
#: content, and passing it to the CLI would be redundant (the CLI recomputes it)
#: while risking a mismatch if the file changed underneath.
_NODE_ONLY_KEYS = frozenset({"judge_spec_hash"})

#: CLI flag name for each config key (the CLI is kebab-case, the params are not).
_CLI_FLAGS = {
    "snapshot": "--snapshot",
    "judge_json": "--judge-json",
    "replicate": "--replicate",
    "experiment_name": "--experiment-name",
    "out_root": "--out-root",
    "cache_root": "--cache-root",
    "sidecar_config": "--sidecar-config",
    "parallelism": "--parallelism",
    "max_instances": "--max-instances",
}

#: Keys that must be present and non-empty for the job to be meaningful.
_REQUIRED = ("snapshot", "judge_json", "out_root", "cache_root")


class RejudgeNode(LeaseBracketMixin, kwdagger.ProcessNode):
    """Apply ONE judge to ONE response snapshot for ONE replicate."""

    name = "rejudge"
    executable = "eval-audit-rejudge-helm"
    in_paths = set()

    out_paths = {
        "out_dpath": ".",
        "done_fname": "DONE",
    }
    primary_out_key = "done_fname"

    # Identity: a different snapshot, judge spec, replicate, experiment name, or
    # instance cap is different work and gets its own job folder. judge_spec_hash
    # (not just the JSON path) is what makes an EDITED judge config a new job.
    algo_params = {
        "snapshot": None,
        "judge_json": None,
        "judge_spec_hash": None,
        "replicate": 0,
        "experiment_name": "open-judge",
        "max_instances": None,
    }

    # Identity-neutral: where results/caches land, how much request concurrency,
    # and which endpoint to lease. None of these change what is computed.
    perf_params = {
        "out_root": None,
        "cache_root": None,
        "sidecar_config": None,
        "parallelism": 8,
        **LEASE_PERF_PARAMS,
    }

    @property
    def command(self) -> str:
        cfg = dict(self.final_config)
        missing = [k for k in _REQUIRED if not str(cfg.get(k) or "").strip()]
        if missing:
            raise ValueError(f"RejudgeNode requires {missing} in its config")

        q = shlex.quote
        parts: list[str] = [self.executable]
        for key, flag in _CLI_FLAGS.items():
            if key in _NODE_ONLY_KEYS or key in _LEASE_KEYS:
                continue
            value = cfg.get(key)
            # None => let the CLI apply its own default (notably max_instances,
            # where a rendered "None" would be parsed as a literal).
            if value is None or str(value).strip() == "":
                continue
            parts.append(f"{flag} {q(str(value))}")

        out_dpath = str(cfg["out_dpath"])
        cli = " \\\n    ".join(parts)
        # Touch the job receipt only on success, so a failed rejudge leaves the
        # node incomplete and kwdagger re-runs it instead of skipping it.
        return f"{cli} && touch {q(f'{out_dpath}/DONE')}"


def rejudge_pipeline():
    """kwdagger pipeline factory: a single leased rejudge node.

    Referenced by ``kwdagger schedule`` as
    ``eval_audit.pipelines.rejudge_pipeline.rejudge_pipeline()``.
    """
    nodes = {"rejudge": RejudgeNode()}
    return kwdagger.Pipeline(nodes)


__all__ = ["RejudgeNode", "rejudge_pipeline"]
