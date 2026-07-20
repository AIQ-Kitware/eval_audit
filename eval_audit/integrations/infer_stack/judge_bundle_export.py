"""Export HELM judge deployment sidecars for the open-judge experiment.

Phase 10 of ``docs/planning/open-judge-plan.md`` (§15): turn a
``JudgeSpec`` + its infer-stack catalog endpoint into a HELM sidecar
directory the rejudge runner registers — ``judge_model_deployments.yaml``
binding ``litellm/<judge>-judge`` to the null-safe OpenAI-compatible
chat client at the LiteLLM gateway, plus a ``judge_bundle_manifest.json``
recording the full provenance. The hand-authored ``judge_model_metadata``
/ ``judge_tokenizer_configs`` sidecars (configs/open_judge/) are copied
in alongside so one directory is a complete HELM config.

Reuses the battle-tested candidate serving path
(``resolve_serving_facts`` + ``_model_deployment_entry``), which is
pure-static (reads catalog.yaml, no live gateway needed) — the gateway
base_url defaults deterministically and the LiteLLM master key is
supplied by the caller at run time. A Qwen judge is NEVER mapped onto an
official GPT-4o/Llama deployment name (§23).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from eval_audit.infra.yaml_io import dump_yaml
from eval_audit.integrations.infer_stack.bundle_export import _model_deployment_entry
from eval_audit.integrations.infer_stack.serving_facts import (
    resolve_serving_facts,
    _resolve_api_key,
)
from eval_audit.judging.specs import JudgeSpec

JUDGE_BUNDLE_SCHEMA_VERSION = 1

# The judge annotators talk chat (the Qwen instruct models); the null-safe
# OpenAI chat client normalizes a reasoning model's content=null -> "".
_JUDGE_PROTOCOL_MODE = "chat"
_JUDGE_ACCESS_KIND = "openai-compatible"
# _model_deployment_entry resolves chat -> this base client; when the judge
# declares an explicit thinking policy we swap to the Qwen subclass that
# enforces it (§13).
_BASE_CHAT_CLIENT = "eval_audit.integrations.helm_clients.NullSafeOpenAIChatClient"
_QWEN_JUDGE_CLIENT = "eval_audit.integrations.helm_clients.QwenJudgeOpenAIChatClient"


def _sha256_file(fpath: Path) -> str:
    return hashlib.sha256(fpath.read_bytes()).hexdigest()


def build_judge_deployment_entry(
    judge: JudgeSpec,
    *,
    config_dir: str | Path | None = None,
    base_url: str | None = None,
    api_key_value: str | None = None,
) -> tuple[dict[str, Any], Any]:
    """Resolve the catalog endpoint and build the model_deployments entry.

    Returns ``(entry, serving_facts)``. ``base_url`` / ``api_key_value``
    default to the deterministic gateway URL and the resolved LiteLLM key
    (supplied at run time on the serving host).
    """
    # Guard the anti-goal (§23) FIRST — before any credential resolution —
    # so an official-name collision always fails loudly.
    lowered = judge.model_deployment.lower()
    if "gpt-4o" in lowered or "llama" in lowered or "gpt4o" in lowered:
        raise ValueError(
            f"judge model_deployment {judge.model_deployment!r} must not reuse an "
            "official GPT-4o/Llama deployment name."
        )
    facts = resolve_serving_facts(judge.lease_endpoint, config_dir=config_dir)
    entry = _model_deployment_entry(
        facts,
        protocol_mode=_JUDGE_PROTOCOL_MODE,
        helm_model_name=judge.model,
        helm_tokenizer_name=judge.model,
        access_kind=_JUDGE_ACCESS_KIND,
        model_deployment_name=judge.model_deployment,
        base_url=base_url,
        api_key_value=api_key_value,
    )
    client_class = entry["client_spec"]["class_name"]
    if client_class != _BASE_CHAT_CLIENT:
        raise ValueError(
            f"judge deployment resolved to unexpected client {client_class!r}; "
            f"expected {_BASE_CHAT_CLIENT!r} (chat protocol). Check the "
            f"catalog endpoint {judge.lease_endpoint!r}."
        )
    # Enforce the declared thinking policy (§13): swap to the Qwen client that
    # sends enable_thinking on every request. server_default sends no switch,
    # so the base null-safe client is left in place.
    if judge.thinking_mode in ("disabled", "enabled"):
        entry["client_spec"]["class_name"] = _QWEN_JUDGE_CLIENT
        entry["client_spec"]["args"]["enable_thinking"] = judge.thinking_mode == "enabled"
    return entry, facts


def export_judge_bundle(
    judges: list[JudgeSpec],
    out_dpath: str | Path,
    *,
    config_dir: str | Path | None = None,
    model_metadata_fpath: str | Path | None = None,
    tokenizer_configs_fpath: str | Path | None = None,
    base_url: str | None = None,
    api_key_value: str | None = None,
    infer_stack_revision: str | None = None,
) -> Path:
    """Write a complete judge sidecar dir for ``judges`` into ``out_dpath``.

    The metadata/tokenizer sidecars default to configs/open_judge/. The
    result registers cleanly via ``register_configs_from_directory``:
    ``model_metadata.yaml`` + ``tokenizer_configs.yaml`` +
    ``model_deployments.yaml``, plus a provenance manifest.
    """
    out_dpath = Path(out_dpath)
    out_dpath.mkdir(parents=True, exist_ok=True)

    repo_configs = Path(__file__).resolve().parents[3] / "configs" / "open_judge"
    model_metadata_src = Path(model_metadata_fpath or repo_configs / "model_metadata.yaml")
    tokenizer_configs_src = Path(
        tokenizer_configs_fpath or repo_configs / "tokenizer_configs.yaml"
    )

    deployment_entries = []
    manifest_judges = []
    for judge in judges:
        entry, facts = build_judge_deployment_entry(
            judge,
            config_dir=config_dir,
            base_url=base_url,
            api_key_value=api_key_value,
        )
        deployment_entries.append(entry)
        manifest_judges.append(
            {
                "judge_id": judge.id,
                "lease_endpoint": judge.lease_endpoint,
                "served_model_name": facts.served_model_name,
                "hf_model_id": facts.hf_model_id,
                "helm_model_name": judge.model,
                "helm_deployment_name": judge.model_deployment,
                "client_class": entry["client_spec"]["class_name"],
                "base_url": entry["client_spec"]["args"].get("base_url"),
                "max_model_len": facts.max_model_len,
                "serving_image": facts.serving_image,
                "dtype": facts.dtype,
                "judge_spec_hash": judge.spec_hash(),
                "thinking_mode": judge.thinking_mode,
                "temperature": judge.temperature,
                "max_tokens": judge.max_tokens,
            }
        )

    # HELM's register_configs_from_directory reads these exact filenames.
    (out_dpath / "model_deployments.yaml").write_text(
        dump_yaml({"model_deployments": deployment_entries}), encoding="utf-8"
    )
    shutil.copyfile(model_metadata_src, out_dpath / "model_metadata.yaml")
    shutil.copyfile(tokenizer_configs_src, out_dpath / "tokenizer_configs.yaml")

    # api_key is a runtime secret — record only whether one was threaded.
    manifest = {
        "artifact_type": "open_judge_bundle",
        "schema_version": JUDGE_BUNDLE_SCHEMA_VERSION,
        "judges": manifest_judges,
        "model_metadata_source": str(model_metadata_src),
        "tokenizer_source": str(tokenizer_configs_src),
        "model_metadata_sha256": _sha256_file(out_dpath / "model_metadata.yaml"),
        "tokenizer_configs_sha256": _sha256_file(out_dpath / "tokenizer_configs.yaml"),
        "api_key_threaded": bool(_resolve_api_key(_JUDGE_ACCESS_KIND, api_key_value=api_key_value)),
        "infer_stack_revision": infer_stack_revision,
    }
    (out_dpath / "judge_bundle_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return out_dpath


__all__ = [
    "JUDGE_BUNDLE_SCHEMA_VERSION",
    "build_judge_deployment_entry",
    "export_judge_bundle",
]
