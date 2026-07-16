"""Benchmark-bundle materialization + export.

Extracted from ``adapter.py`` (R-3, pure relocation). Builds the
model_deployments.yaml entries, HELM alias assertions, manifest docs, and the
materialize/export entry points. Imports leaf modules (presets / serving_facts
/ freeze / discovery); adapter.py re-exports the public surface.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from eval_audit.infra.paths import audit_store_root, repo_root
from eval_audit.infra.yaml_io import dump_yaml
from eval_audit.integrations.infer_stack.presets import PRESET_CONFIGS
from eval_audit.integrations.infer_stack.serving_facts import (
    DEFAULT_LEASE_TTL,
    ServingFacts,
    resolve_serving_facts,
    _benchmark_client_class,
    _default_deployment_name,
    _default_gateway_base_url,
    _infer_stack_config_root,
    _resolve_api_key,
)
from eval_audit.integrations.infer_stack.freeze import _freeze_run_spec_sources


def _model_deployment_entry(
    facts: ServingFacts,
    *,
    protocol_mode: str,
    helm_model_name: str | None = None,
    helm_tokenizer_name: str | None = None,
    helm_max_sequence_and_generated_tokens_length: int | None = None,
    access_kind: str | None = None,
    model_deployment_name: str | None = None,
    base_url: str | None = None,
    api_key_value: str | None = None,
) -> dict[str, Any]:
    # default-B: the front door is the LiteLLM gateway (openai-compatible) for
    # every preset; vllm-direct is a fallback-only marker (migration plan §5.G3).
    kind = access_kind or "openai-compatible"
    served_name = facts.served_model_name
    if facts.max_model_len is None:
        raise ValueError(
            f"catalog endpoint {facts.endpoint!r} declares no runtime.max_model_len; "
            "set endpoints.<name>.runtime.max_model_len in catalog.yaml so HELM can "
            "size the prompt+generation budget."
        )
    max_model_len = int(facts.max_model_len)
    client_class = _benchmark_client_class(protocol_mode, kind)
    if kind == "vllm-direct":
        # vllm-direct talks to the vLLM server directly and sends api_key="EMPTY";
        # it MUST NOT fall back to the auth-protected LiteLLM gateway base_url
        # (the client cannot authenticate there — requests would 401). Require an
        # explicit --base-url pointing at the vLLM server.
        if not base_url:
            raise ValueError(
                f"access kind 'vllm-direct' for deployment "
                f"{model_deployment_name or _default_deployment_name(served_name, kind)!r} "
                "requires an explicit --base-url (the vLLM server address); it must not "
                "default to the LiteLLM gateway, which the vllm-direct client cannot reach."
            )
        resolved_base_url = base_url
    else:
        resolved_base_url = base_url or _default_gateway_base_url()
    entry = {
        "name": model_deployment_name or _default_deployment_name(served_name, kind),
        # HELM-domain aliases are preset-authoritative; the catalog hf_model_id is
        # only a last-resort fallback (and _assert_helm_aliases_exist fails loudly
        # if it isn't a registered HELM alias — no silent wrong alias).
        "model_name": helm_model_name or facts.hf_model_id,
        "tokenizer_name": helm_tokenizer_name or facts.hf_model_id,
        "max_sequence_length": max_model_len,
        # vLLM-style servers enforce the total prompt+generation budget against max-model-len.
        "max_sequence_and_generated_tokens_length": int(
            helm_max_sequence_and_generated_tokens_length or max_model_len
        ),
        "client_spec": {
            "class_name": client_class,
            "args": {
                "base_url": resolved_base_url,
            },
        },
    }
    if kind == "vllm-direct":
        entry["client_spec"]["args"]["vllm_model_name"] = served_name
    else:
        resolved_api_key = _resolve_api_key(kind, api_key_value=api_key_value)
        entry["client_spec"]["args"]["api_key"] = resolved_api_key
        # The LiteLLM gateway registers each model under its endpoint name (the
        # served name), so the client must request exactly that (C-3 in the plan:
        # openai_model_name == endpoint public_name == served name).
        entry["client_spec"]["args"]["openai_model_name"] = served_name
    return entry


#: The era shim's OpenAI-compatible completions client (installed inside the era
#: image only). Deployments generated for an era run bind the official model name
#: to this client via the era model_deployment registry.
_ERA_CLIENT_CLASS = "helm_era_shim.openai_compat_client.OpenAICompatCompletionsClient"


def _model_deployment_entry_era(
    facts: ServingFacts,
    *,
    helm_model_name: str,
    helm_tokenizer_name: str | None = None,
    base_url: str | None = None,
    api_key_value: str | None = None,
) -> dict[str, Any]:
    """Build an ERA-schema (pre-v0.5) ``model_deployments.yaml`` entry.

    Differences from the modern :func:`_model_deployment_entry`:

    * The deployment ``name`` is the **exact official model name** (equal to the
      run_spec.json's ``adapter_spec.model``). Era replay is verbatim by-name —
      there is no ``model_deployment`` field to rewrite — so the registered name
      must match the official model, not a synthesized local deployment name.
    * ``client_spec.class_name`` is the era shim client; ``args`` carry
      ``base_url`` + ``openai_model_name`` (the served name) + ``api_key``. The
      key MUST live in args, NOT solely in the era ``credentials.conf``: pyhocon
      path-splits dotted model names (``eleutherai/pythia-6.9b`` → path
      ``eleutherai/pythia-6`` → ``9b``), so a credentials.conf lookup by the raw
      model string is unreachable (Finding 2). At v0.3.0, ``api_key`` present in
      args also stops ``inject_object_spec_args`` from firing the
      ``provide_api_key`` provider (which would hit that broken lookup). The shim
      still writes a nested-key credentials.conf entry for v0.2.4's eager
      pre-construction credential check. ``"EMPTY"`` is the shim client's
      unset sentinel (no ``Authorization`` header — correct for a direct vLLM
      server); a real key is threaded for a gateway that authenticates.
    * All five cattrs-no-defaults keys are emitted explicitly (``model_name`` /
      ``tokenizer_name`` / ``max_sequence_length``) so ``cattrs.structure`` at the
      era succeeds. ``tokenizer_name`` and ``max_sequence_length`` MUST be set:
      registering a deployment routes the era ``WindowServiceFactory`` down the
      ``if get_model_deployment(model): ...`` branch, which at v0.2.4 hard-raises
      ``"Tokenizer name must be set on model deplyment"`` when the deployment's
      ``tokenizer_name`` is null (there is no auto-inference — the model-name
      GPTNeoX/etc. fallback is only reached when NO deployment is registered), and
      at v0.3.0 builds a ``DefaultWindowService`` that needs both. The value comes
      from the preset's ``helm_tokenizer_name`` (e.g. ``EleutherAI/gpt-neox-20b``
      for the redpajama/pythia GPT-NeoX family — what ``GPTNeoXWindowService``
      used officially); the catalog ``max_model_len`` supplies the window.

    Requires an explicit ``base_url`` (Finding 5): the era shim client cannot
    authenticate at the LiteLLM gateway with the ``EMPTY`` sentinel — every
    request would 401 — so it must never silently default there, mirroring the
    modern ``vllm-direct`` guard.
    """
    if not helm_model_name:
        raise ValueError(
            "era model deployment requires helm_model_name (it must equal the "
            "official run_spec.json adapter_spec.model — era replay is by-name)."
        )
    if not base_url:
        raise ValueError(
            f"era model deployment for {helm_model_name!r} requires an explicit "
            "--base-url (the served vLLM/gateway address); it must not default to "
            "the LiteLLM gateway, which the era shim client cannot authenticate "
            "against with the EMPTY sentinel (every request would 401)."
        )
    if facts.max_model_len is None:
        raise ValueError(
            f"era model deployment for {helm_model_name!r}: catalog endpoint "
            f"{facts.endpoint!r} declares no runtime.max_model_len; set it in "
            "catalog.yaml so the era WindowService can size the prompt window."
        )
    return {
        "name": helm_model_name,
        "model_name": helm_model_name,
        # Both REQUIRED once a deployment is registered: v0.2.4's
        # WindowServiceFactory raises "Tokenizer name must be set on model
        # deplyment" for a null tokenizer_name, and v0.3.0's DefaultWindowService
        # needs both. Preset-authoritative tokenizer (the official era alias, e.g.
        # EleutherAI/gpt-neox-20b); catalog max_model_len sizes the window. Falls
        # back to the model's own HF tokenizer only if the preset omits it.
        "tokenizer_name": helm_tokenizer_name or facts.hf_model_id,
        "max_sequence_length": int(facts.max_model_len),
        "client_spec": {
            "class_name": _ERA_CLIENT_CLASS,
            "args": {
                "base_url": base_url,
                # vLLM's served model name (what the /v1/completions call sends).
                "openai_model_name": facts.served_model_name,
                # In args, not credentials.conf — see the docstring (Finding 2).
                "api_key": api_key_value or "EMPTY",
            },
        },
    }


def _helm_config_paths() -> tuple[Path, Path]:
    helm_root = repo_root() / "submodules" / "helm" / "src" / "helm" / "config"
    return helm_root / "model_metadata.yaml", helm_root / "tokenizer_configs.yaml"


def _assert_helm_aliases_exist(
    model_name: str,
    tokenizer_name: str,
    *,
    model_metadata_fpath: str | None = None,
    tokenizer_configs_fpath: str | None = None,
) -> None:
    """Assert the HELM model/tokenizer aliases resolve at export time.

    The universe is the vendored HELM's builtin registry UNION the preset's
    optional registry sidecars (``model_metadata_fpath`` /
    ``tokenizer_configs_fpath``) — the same union helm-run itself sees once the
    materializer copies the sidecars into ``--local-path``. Net-new ids
    therefore need only sidecar files, never a HELM-source edit.
    """
    import yaml

    model_metadata_path, tokenizer_configs_path = _helm_config_paths()
    model_docs = yaml.safe_load(model_metadata_path.read_text(encoding="utf-8")) or {}
    tokenizer_docs = yaml.safe_load(tokenizer_configs_path.read_text(encoding="utf-8")) or {}
    known_models = {item.get("name") for item in model_docs.get("models", []) or []}
    known_tokenizers = {item.get("name") for item in tokenizer_docs.get("tokenizer_configs", []) or []}
    if model_metadata_fpath:
        sidecar_path = repo_root() / model_metadata_fpath
        sidecar_docs = yaml.safe_load(sidecar_path.read_text(encoding="utf-8")) or {}
        known_models |= {item.get("name") for item in sidecar_docs.get("models", []) or []}
    if tokenizer_configs_fpath:
        sidecar_path = repo_root() / tokenizer_configs_fpath
        sidecar_docs = yaml.safe_load(sidecar_path.read_text(encoding="utf-8")) or {}
        known_tokenizers |= {
            item.get("name") for item in sidecar_docs.get("tokenizer_configs", []) or []
        }
    if model_name not in known_models:
        raise ValueError(
            f"HELM model alias missing for {model_name!r}; register it in the vendored "
            "HELM or ship a model_metadata.yaml sidecar (preset model_metadata_fpath) "
            "before launching the run."
        )
    if tokenizer_name not in known_tokenizers:
        raise ValueError(
            f"HELM tokenizer alias missing for {tokenizer_name!r}; register it in the "
            "vendored HELM or ship a tokenizer_configs.yaml sidecar (preset "
            "tokenizer_configs_fpath) before launching the run."
        )


def _profile_specs(profile: str, preset_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    preset_profiles = preset_cfg.get("profiles")
    if preset_profiles:
        return [dict(item) for item in preset_profiles]
    # Single-profile (flat) presets carry the same HELM overrides the
    # `profiles:` list form does — propagate them so a flat preset can pin a
    # HELM model/tokenizer alias and, importantly, reserve headroom below
    # max-model-len via `helm_max_sequence_and_generated_tokens_length` (the
    # live vLLM chat path needs a few tokens beyond HELM's nominal budget for
    # the chat-template wrapper; see the OLMo presets).
    return [{
        "profile": preset_cfg.get("profile", profile),
        "access_kind": preset_cfg.get("access_kind"),
        # G2: the chat-vs-completions distinction used to live in the profile
        # name (e.g. `-completions` vs `-chat`); the catalog has no such field,
        # so it is now an explicit, required preset fact (no default — see the
        # required check in materialize_benchmark_bundle).
        "protocol_mode": preset_cfg.get("protocol_mode"),
        "model_deployment_name": preset_cfg.get("model_deployment_name"),
        "helm_model_name": preset_cfg.get("helm_model_name"),
        "helm_tokenizer_name": preset_cfg.get("helm_tokenizer_name"),
        "helm_max_sequence_and_generated_tokens_length": preset_cfg.get(
            "helm_max_sequence_and_generated_tokens_length"
        ),
    }]


def _maybe_repo_relative(target: Path) -> str:
    try:
        return str(target.resolve().relative_to(repo_root().resolve()))
    except ValueError:
        return str(target.resolve())


# Containerized-execution opt-in fields a preset's smoke/full manifest spec may
# declare; when present they are forwarded verbatim into the generated bundle
# manifest so Stage 3 runs HELM inside the pinned image (see
# eval_audit/manifests/models.py and docs/container-execution.md).
_CONTAINER_SPEC_KEYS = (
    "container_image",
    "container_runtime",
    "hf_cache_dir",
    "container_gpus",
    "container_shm_size",
    "container_ipc_host",
    "container_mounts",
    "container_network",
)


def _lease_facts(
    facts: list[ServingFacts],
    model_entries: list[dict[str, Any]],
    *,
    preset_cfg: dict[str, Any],
    lease_catalog: Path | str | None,
) -> dict[str, Any]:
    """Build the per-run GPU-lease facts baked into the generated manifest.

    These let ``eval-audit-run --lease`` bracket each HELM run with an
    ``infer-stack acquire``/``release`` (the high-throughput fan-out, plan §13).
    A single-endpoint manifest gets a scalar ``lease_endpoint``; a multi-endpoint
    one gets a ``lease_endpoints`` ``{deployment_name: catalog_endpoint}`` map
    keyed by the model_deployments.yaml entry name its run-entries reference.

    Asserts the **C-3 name chain**: the served name HELM requests must equal the
    catalog endpoint name the lease acquires (and the no-blip LiteLLM gateway
    routes by). They are equal whenever the catalog leaves ``served_name`` /
    ``public_name`` unset (it defaults to the endpoint name); a divergent
    override silently misroutes every leased run, so fail loud here instead.
    """
    for fact in facts:
        if fact.served_model_name != fact.endpoint:
            raise ValueError(
                f"C-3 name-chain violation for catalog endpoint {fact.endpoint!r}: "
                f"it serves under name {fact.served_model_name!r}. A lease acquires "
                "the endpoint name while HELM requests the served name, and the "
                "no-blip LiteLLM gateway routes by endpoint name — so they must "
                "match. Remove the endpoint's served_name/public_name override (it "
                "defaults to the endpoint name) before scheduling a leased run."
            )
    out: dict[str, Any] = {}
    if len(facts) == 1:
        out["lease_endpoint"] = facts[0].endpoint
    else:
        out["lease_endpoints"] = {
            entry["name"]: fact.endpoint
            for entry, fact in zip(model_entries, facts, strict=True)
        }
    out["lease_ttl"] = str(preset_cfg.get("lease_ttl") or DEFAULT_LEASE_TTL)
    if lease_catalog is not None:
        out["lease_catalog"] = str(Path(lease_catalog).resolve())
    return out


def _manifest_doc(
    *,
    spec: dict[str, Any],
    model_deployments_fpath: str,
    lease_facts: dict[str, Any] | None = None,
    from_run_spec: bool = False,
    precomputed_root: str | None = None,
    model_deployment: str | None = None,
    run_spec_sources: list[dict[str, Any]] | None = None,
    era: str | None = None,
    model_metadata_fpath: str | None = None,
    tokenizer_configs_fpath: str | None = None,
) -> dict[str, Any]:
    # From-spec replay: the generated manifest must carry from_run_spec: true and a
    # precomputed_root (the recipe SOURCE the bridge requires). Because this builder
    # emits a FIXED dict, threading the two fields here is the only way a preset/CLI
    # value reaches the manifest — adding precomputed_root to a preset block alone
    # would be silently dropped (it is not a _CONTAINER_SPEC_KEY), landing on the
    # run-entry path with no error (migration plan Change 2a). The run-entry path
    # keeps precomputed_root: None and omits from_run_spec (its manifest default).
    resolved_precomputed_root = (
        (precomputed_root or spec.get("precomputed_root")) if from_run_spec else None
    )
    doc = {
        "schema_version": 1,
        "experiment_name": spec["experiment_name"],
        "description": spec["description"],
        "run_entries": spec["run_entries"],
        "max_eval_instances": spec["max_eval_instances"],
        "suite": spec["suite"],
        "mode": "compute_if_missing",
        "materialize": "symlink",
        "backend": "tmux",
        "devices": spec.get("devices", "0"),
        "tmux_workers": spec.get("tmux_workers", 1),
        "local_path": "prod_env",
        "precomputed_root": resolved_precomputed_root,
        "require_per_instance_stats": True,
        "model_deployments_fpath": model_deployments_fpath,
        "enable_huggingface_models": [],
        "enable_local_huggingface_models": [],
    }
    # HELM registry sidecars (net-new model/tokenizer ids): only emitted when the
    # preset declares them, so existing manifests stay byte-compatible.
    if model_metadata_fpath is not None:
        doc["model_metadata_fpath"] = model_metadata_fpath
    if tokenizer_configs_fpath is not None:
        doc["tokenizer_configs_fpath"] = tokenizer_configs_fpath
    if from_run_spec:
        doc["from_run_spec"] = True
        # Deployment-rewrite target: the LOCAL deployment name the replay records
        # into the produced run_spec.json (so the audit reports same_deployment=no).
        # Only meaningful under from_run_spec; omitted otherwise so the run-entry
        # manifest stays byte-compatible. None => pure by-name (the from-spec CLI
        # keeps the official deployment name).
        if model_deployment is not None:
            doc["model_deployment"] = model_deployment
        # Exact-path replay (rel-path plan §4.5): frozen run_spec_sources. When
        # present, the bridge addresses each official run by its pinned rel-path
        # and the materializer applies the substitutions host-side — superseding
        # run-entry token discovery (run_entries is kept as labels). Per-source
        # model_deployment lifts the single-deployment restriction below.
        if run_spec_sources is not None:
            doc["run_spec_sources"] = run_spec_sources
    # Era (pre-v0.5) replay: pin the measurement instrument. The bridge selects
    # the era pipeline + guards the image's org.aiq.era label against this. None
    # (the modern default) is omitted so modern manifests stay byte-compatible.
    if era is not None:
        doc["era"] = era
    for key in _CONTAINER_SPEC_KEYS:
        if key in spec:
            doc[key] = spec[key]
    # Lease facts are inert until `eval-audit-run --lease` reads them; baking
    # them in keeps the manifest self-describing about which endpoint each run
    # leases.
    if lease_facts:
        doc.update(lease_facts)
    return doc


def _write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(data), encoding="utf-8")


def _write_alias(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _resolve_preset_cfg(preset: str | None) -> dict[str, Any]:
    """Look up a preset's config block, failing fast on an unknown key.

    A ``None`` preset means "no preset" (transport/profile come from explicit
    args) and resolves to an empty block. A *non-empty* preset that is not a
    registered key is a caller typo — raise with the known-presets list rather
    than silently proceeding with an empty config (which would drop the
    preset's access_kind / protocol_mode / manifest and produce a subtly wrong
    bundle). Mirrors cli/check_precomputed_discovery's message style.
    """
    if not preset:
        return {}
    if preset not in PRESET_CONFIGS:
        raise ValueError(
            f"unknown preset {preset!r}; known: {', '.join(sorted(PRESET_CONFIGS))}"
        )
    return PRESET_CONFIGS[preset]


def materialize_benchmark_bundle(
    *,
    facts: list[ServingFacts],
    output_dir: Path,
    preset: str | None = None,
    profile_specs: list[dict[str, Any]] | None = None,
    access_kind: str | None = None,
    protocol_mode: str | None = None,
    base_url: str | None = None,
    api_key_value: str | None = None,
    lease_catalog: Path | str | None = None,
    from_run_spec: bool = False,
    precomputed_root: str | None = None,
    freeze_rel_paths: bool = False,
    era: str | None = None,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    preset_cfg = _resolve_preset_cfg(preset)
    specs = profile_specs or _profile_specs("", preset_cfg)
    # Era (pre-v0.5) mode: CLI --era wins, else the preset may declare one. When
    # set, the generated deployments use the era schema + era shim client, the
    # modern HELM-alias assertion is skipped (it validates against the modern
    # submodule — the wrong universe; the shim preflight is the loud check), and
    # replay is verbatim by-name (no model_deployment rewrite).
    #
    # B2 invariant: this is the ONLY era decision point in the exporter.
    # Every era-vs-modern behavior below derives from the single `era_mode`
    # flag — do not consult the era key (or re-derive the mode) anywhere else
    # in this function.
    resolved_era = era or preset_cfg.get("era")
    era_mode = resolved_era is not None
    # HELM registry sidecars (net-new model/tokenizer ids): preset-level,
    # repo-relative paths. They widen the alias-assert universe below and are
    # forwarded into both generated manifests (the bridge resolves + mounts them
    # and the materializer copies them into prod_env at run time).
    sidecar_model_metadata_fpath = preset_cfg.get("model_metadata_fpath")
    sidecar_tokenizer_configs_fpath = preset_cfg.get("tokenizer_configs_fpath")
    model_entries = []
    selected_accesses = []
    for fact, spec in zip(facts, specs, strict=True):
        selected_kind = access_kind or spec.get("access_kind") or preset_cfg.get("access_kind") or "openai-compatible"
        # protocol_mode is required — there is no default. Picking chat vs
        # completions wrong silently breaks reproduction (a base model served as
        # chat gets its prompt chat-templated and emits garbage; see the OLMo-7B
        # "The" failure). Every preset/profile must declare it explicitly so the
        # choice is a conscious, reviewable fact rather than an accident.
        # Precedence: caller override → profile spec → preset.
        resolved_protocol_mode = (
            protocol_mode or spec.get("protocol_mode") or preset_cfg.get("protocol_mode")
        )
        if resolved_protocol_mode is None:
            raise ValueError(
                f"protocol_mode is required but unset for profile "
                f"{spec.get('profile')!r} (preset {preset!r}); set "
                f"'protocol_mode' to 'completions' (base/text-completion models) "
                f"or 'chat' (instruct/chat models) in the preset or profile spec, "
                f"or pass an explicit override (--protocol-mode)."
            )
        if resolved_protocol_mode not in ("chat", "completions"):
            raise ValueError(
                f"protocol_mode for profile {spec.get('profile')!r} (preset "
                f"{preset!r}) must be 'chat' or 'completions', got "
                f"{resolved_protocol_mode!r}."
            )
        # The era shim client implements /v1/completions ONLY. Rather than let
        # protocol_mode be required-but-dead on the era fork, assert it is
        # 'completions' so a mis-declared era preset fails loud at export time.
        if era_mode and resolved_protocol_mode != "completions":
            raise ValueError(
                f"era replay only supports protocol_mode 'completions' (the era "
                f"shim client speaks /v1/completions only), but profile "
                f"{spec.get('profile')!r} (preset {preset!r}) declares "
                f"{resolved_protocol_mode!r}."
            )
        # The generated model_deployments.yaml binds the bundle's NATIVE local
        # deployment name on BOTH paths (run-entry and from-spec). Under from-spec
        # the manifest's `model_deployment` field (set below) names this same entry
        # and the replay rewrites the run_spec.json's adapter_spec.model_deployment
        # to it — so the produced run records the local endpoint (same_deployment=no)
        # with the rewrite target and the registration agreeing by construction.
        # This supersedes the earlier by-name rekey to the official name; see
        # docs/historical/planning/from-spec-deployment-rewrite-plan.md Change 5.
        deployment_name = spec.get("model_deployment_name")
        if era_mode:
            # Era deployment: bind the OFFICIAL model name to the era shim client
            # (verbatim by-name). No modern alias assertion (wrong submodule
            # universe) and no rewrite target.
            model_entries.append(
                _model_deployment_entry_era(
                    fact,
                    helm_model_name=spec.get("helm_model_name"),
                    helm_tokenizer_name=spec.get("helm_tokenizer_name"),
                    base_url=base_url,
                    api_key_value=api_key_value,
                )
            )
        else:
            model_entries.append(
                _model_deployment_entry(
                    fact,
                    protocol_mode=resolved_protocol_mode,
                    helm_model_name=spec.get("helm_model_name"),
                    helm_tokenizer_name=spec.get("helm_tokenizer_name"),
                    helm_max_sequence_and_generated_tokens_length=spec.get("helm_max_sequence_and_generated_tokens_length"),
                    access_kind=selected_kind,
                    model_deployment_name=deployment_name,
                    base_url=base_url,
                    api_key_value=api_key_value,
                )
            )
            _assert_helm_aliases_exist(
                model_entries[-1]["model_name"],
                model_entries[-1]["tokenizer_name"],
                model_metadata_fpath=sidecar_model_metadata_fpath,
                tokenizer_configs_fpath=sidecar_tokenizer_configs_fpath,
            )
        # The old contract carried a rich access dict; under default-B the only
        # facts worth recording are the resolved transport (for bundle.yaml
        # traceability — nothing downstream parses it).
        selected_accesses.append(
            {
                "kind": selected_kind,
                "base_url": model_entries[-1]["client_spec"]["args"]["base_url"],
                "request_model_name": fact.served_model_name,
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    model_deployments = {"model_deployments": model_entries}
    # P0-5: content-address the generated filename, mirroring the run_spec
    # materializer's ``run_spec.<content-hash>.json`` convention. The path
    # string enters kwdagger job identity via ``helm.model_deployments_fpath``;
    # a fixed name meant a re-export with changed semantics (protocol_mode
    # chat-vs-completions, tokenizer alias, max sequence length) reused GPU
    # results computed under the OLD config (skip_existing defaults True). The
    # docker node passes this file by explicit ``--model_deployments_fpath``
    # (mounted at its own path), so HELM reads it by path, not by fixed name.
    _md_text = dump_yaml(model_deployments)
    _md_hash = hashlib.sha256(_md_text.encode("utf-8")).hexdigest()[:16]
    model_deployments_path = output_dir / f"model_deployments.{_md_hash}.yaml"
    model_deployments_path.parent.mkdir(parents=True, exist_ok=True)
    model_deployments_path.write_text(_md_text, encoding="utf-8")
    # The YAML embeds the resolved LITELLM_MASTER_KEY in plaintext under
    # client_spec.args.api_key: HELM's OpenAIClient reads that arg literally
    # (helm.clients.openai_client.OpenAIClient.__init__ forwards it straight to
    # OpenAI(api_key=...)); this vendoring has no ${ENV} indirection, so the
    # live key must sit on disk for the runner container to consume. Tighten
    # perms to owner-only immediately after writing so a default umask does not
    # leave the key world/group-readable. See README note on bundle secrets.
    try:
        os.chmod(model_deployments_path, 0o600)
    except OSError:
        # chmod can legitimately fail on some filesystems (e.g. certain mounts);
        # do not abort bundle materialization over a best-effort perms tighten.
        pass
    # Never fail silently when the live key remains readable to others: an
    # operator on a chmod-rejecting mount must see it to relocate the bundle.
    if model_deployments_path.stat().st_mode & 0o077:
        sys.stderr.write(
            f"WARNING: could not restrict permissions on {model_deployments_path} "
            "(filesystem rejected chmod 0600); it embeds the live "
            "LITELLM_MASTER_KEY — move the bundle to a private filesystem or "
            "rotate the key.\n"
        )

    model_deployments_fpath = _maybe_repo_relative(model_deployments_path)
    smoke_spec = preset_cfg.get(
        "smoke_manifest",
        {
            "experiment_name": f"{facts[0].served_model_name}-smoke",
            "description": f"Machine-local benchmark smoke manifest for {facts[0].served_model_name}.",
            "run_entries": [
                f"ifeval:model={entry['model_name']},model_deployment={entry['name']}"
                for entry in model_entries
            ],
            "suite": f"{facts[0].served_model_name}-smoke",
            "max_eval_instances": 5,
        },
    )
    full_spec = preset_cfg.get(
        "full_manifest",
        {
            "experiment_name": f"{facts[0].served_model_name}-full",
            "description": f"Machine-local benchmark full manifest for {facts[0].served_model_name}.",
            "run_entries": [
                f"ifeval:model={entry['model_name']},model_deployment={entry['name']}"
                for entry in model_entries
            ],
            "suite": f"{facts[0].served_model_name}-full",
            "max_eval_instances": 1000,
        },
    )
    lease_facts = _lease_facts(
        facts,
        model_entries,
        preset_cfg=preset_cfg,
        lease_catalog=lease_catalog,
    )
    # From-spec replay records the LOCAL deployment so the audit reports
    # same_deployment=no. The rewrite target is the bundle's own deployment name —
    # the exact name model_deployments.yaml registers — so target and registration
    # agree by construction (the §3 invariant holds with no drift). On the
    # *discovery* from-spec path only a single-deployment bundle has an unambiguous
    # manifest-level target; a multi-deployment bundle stays pure by-name. The
    # *exact-path* path (--freeze-rel-paths) instead carries a per-run rewrite
    # target inside each frozen source, so this restriction does not apply there.
    # Era replay is verbatim by-name: there is no model_deployment to rewrite
    # (and the materializer guard would reject inserting the novel field). So the
    # era path never sets a rewrite target regardless of deployment count.
    rewrite_deployment = (
        None
        if era_mode
        else (
            model_entries[0]["name"]
            if from_run_spec and len(model_entries) == 1
            else None
        )
    )

    # Exact-path replay (rel-path plan §4.5): resolve each run-entry to its pinned
    # rel-path NOW, against the corpus snapshot, and freeze run_spec_sources into the
    # generated manifests. Discovery (token-subset) runs exactly here, once. The
    # corpus is enumerated once per distinct root and shared across smoke/full.
    smoke_sources = full_sources = None
    if freeze_rel_paths:
        from eval_audit.integrations.infer_stack import discovery as dc

        runs_cache: dict[str, list[Any]] = {}

        def _runs_for(root: str) -> list[Any]:
            if root not in runs_cache:
                runs_cache[root] = dc._enumerate_runs(Path(root))
            return runs_cache[root]

        smoke_root = precomputed_root or smoke_spec.get("precomputed_root")
        full_root = precomputed_root or full_spec.get("precomputed_root")
        if not smoke_root or not full_root:
            raise ValueError(
                "--freeze-rel-paths requires a precomputed_root (per-spec or "
                "--precomputed-root): it is the corpus the rel-paths resolve against."
            )
        smoke_sources = _freeze_run_spec_sources(
            smoke_spec, precomputed_root=smoke_root, model_entries=model_entries,
            lease_facts=lease_facts, runs=_runs_for(smoke_root),
            omit_model_deployment=era_mode,
        )
        full_sources = _freeze_run_spec_sources(
            full_spec, precomputed_root=full_root, model_entries=model_entries,
            lease_facts=lease_facts, runs=_runs_for(full_root),
            omit_model_deployment=era_mode,
        )

    benchmark_smoke_manifest = _manifest_doc(
        spec=smoke_spec,
        model_deployments_fpath=model_deployments_fpath,
        lease_facts=lease_facts,
        from_run_spec=from_run_spec,
        precomputed_root=precomputed_root,
        model_deployment=rewrite_deployment,
        run_spec_sources=smoke_sources,
        era=resolved_era,
        model_metadata_fpath=sidecar_model_metadata_fpath,
        tokenizer_configs_fpath=sidecar_tokenizer_configs_fpath,
    )
    benchmark_full_manifest = _manifest_doc(
        spec=full_spec,
        model_deployments_fpath=model_deployments_fpath,
        lease_facts=lease_facts,
        from_run_spec=from_run_spec,
        precomputed_root=precomputed_root,
        model_deployment=rewrite_deployment,
        run_spec_sources=full_sources,
        era=resolved_era,
        model_metadata_fpath=sidecar_model_metadata_fpath,
        tokenizer_configs_fpath=sidecar_tokenizer_configs_fpath,
    )
    benchmark_smoke_path = output_dir / "benchmark_smoke_manifest.yaml"
    benchmark_full_path = output_dir / "benchmark_full_manifest.yaml"
    _write_yaml(benchmark_smoke_path, benchmark_smoke_manifest)
    _write_yaml(benchmark_full_path, benchmark_full_manifest)

    smoke_manifest_path = output_dir / "smoke_manifest.yaml"
    full_manifest_path = output_dir / "full_manifest.yaml"
    _write_alias(benchmark_smoke_path, smoke_manifest_path)
    _write_alias(benchmark_full_path, full_manifest_path)

    bundle = {
        "target": "crfm_helm_benchmark",
        "benchmark": {
            "preset": preset,
            "model_deployment_name": model_entries[0]["name"] if len(model_entries) == 1 else None,
            "model_deployment_names": [entry["name"] for entry in model_entries],
            "model_deployments_path": str(model_deployments_path),
            "model_deployments_fpath": model_deployments_fpath,
        },
        "artifacts": {
            "model_deployments": str(model_deployments_path),
            "benchmark_smoke_manifest": str(benchmark_smoke_path),
            "benchmark_full_manifest": str(benchmark_full_path),
        },
    }
    if len(facts) == 1:
        bundle["profile"] = facts[0].endpoint
        bundle["selected_access"] = selected_accesses[0]
    else:
        bundle["profiles"] = [fact.endpoint for fact in facts]
        bundle["selected_accesses"] = selected_accesses
    bundle_path = output_dir / "bundle.yaml"
    _write_yaml(bundle_path, bundle)
    return {
        "bundle_dir": output_dir,
        "bundle_path": bundle_path,
        "model_deployments_path": model_deployments_path,
        "benchmark_smoke_manifest_path": benchmark_smoke_path,
        "benchmark_full_manifest_path": benchmark_full_path,
        "smoke_manifest_path": smoke_manifest_path,
        "full_manifest_path": full_manifest_path,
        "bundle": bundle,
    }


def export_benchmark_bundle(
    profile: str,
    *,
    preset: str | None = None,
    bundle_root: Path | None = None,
    backend: str | None = None,
    config_dir: Path | None = None,
    access_kind: str | None = None,
    protocol_mode: str | None = None,
    base_url: str | None = None,
    api_key_value: str | None = None,
    # Deprecated: ``simulate_hardware`` was the GPU-simulation knob for the old
    # contract resolver; the catalog resolver is hardware-free, so it is now
    # accept-and-ignore for one release (migration plan §5.G5). ``vllm_root`` is
    # the legacy name for ``config_dir`` (the infer-stack config dir holding
    # catalog.yaml); kept as an alias.
    simulate_hardware: str | None = None,
    vllm_root: Path | None = None,
    from_run_spec: bool = False,
    precomputed_root: str | None = None,
    freeze_rel_paths: bool = False,
    era: str | None = None,
) -> dict[str, Any]:
    # Exact-path replay is a from-spec variant: freezing rel-paths implies it.
    if freeze_rel_paths:
        from_run_spec = True
    preset_cfg = _resolve_preset_cfg(preset)
    specs = _profile_specs(profile, preset_cfg)
    resolved_config_dir = config_dir or vllm_root
    facts = [
        resolve_serving_facts(
            spec["profile"],
            config_dir=resolved_config_dir,
        )
        for spec in specs
    ]
    # The catalog the lease facts point at — the same one the facts resolved
    # against. Baked into the manifest as an absolute path so `infer-stack
    # acquire --catalog` works from any kwdagger job cwd.
    lease_catalog = _infer_stack_config_root(resolved_config_dir) / "catalog.yaml"
    if bundle_root is None:
        # Allow presets to override the target bundle root path. If the
        # preset sets `bundle_root`, interpret absolute paths directly
        # and relative paths as repo-root-relative. Otherwise fall back
        # to the default audit store location.
        preset_bundle_root = preset_cfg.get("bundle_root")
        if preset_bundle_root:
            bpath = Path(preset_bundle_root)
            if not bpath.is_absolute():
                bpath = repo_root() / preset_bundle_root
            bundle_root = bpath.resolve()
        else:
            bundle_name = preset_cfg.get("bundle_name") or specs[0]["profile"].replace("-", "_")
            bundle_root = audit_store_root() / "local-bundles" / bundle_name
    return materialize_benchmark_bundle(
        facts=facts,
        output_dir=bundle_root,
        preset=preset,
        profile_specs=specs,
        access_kind=access_kind,
        protocol_mode=protocol_mode,
        base_url=base_url,
        api_key_value=api_key_value,
        lease_catalog=lease_catalog,
        from_run_spec=from_run_spec,
        precomputed_root=precomputed_root,
        freeze_rel_paths=freeze_rel_paths,
        era=era,
    )
