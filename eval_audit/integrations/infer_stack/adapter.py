from __future__ import annotations

import hashlib
import importlib
import os
import shutil
import sys
import subprocess
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval_audit.infra.paths import audit_store_root, repo_root
from eval_audit.infra.yaml_io import dump_yaml


PRESET_CONFIGS: dict[str, dict[str, Any]] = {
    "gpt_oss_20b_core_grid": {
        "profile": "gpt-oss-20b-completions-dp4",
        "bundle_name": "gpt_oss_20b_core_grid",
        "access_kind": "openai-compatible",
        # G2: the old `-completions` profile name encoded this; now explicit.
        # (Frozen/archival preset — no catalog entry ships for it.)
        "protocol_mode": "completions",
        "model_deployment_name": "litellm/gpt-oss-20b-local",
        "smoke_manifest": {
            "experiment_name": "audit-gpt-oss-20b-core-grid-smoke",
            "description": "Smoke-test for gpt-oss-20b on the core 14 reproducibility benchmarks.",
            "run_entries": [
                "boolq:model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
            ],
            "suite": "audit-gpt-oss-20b-core-grid-smoke",
            "max_eval_instances": 5,
        },
        "full_manifest": {
            "experiment_name": "audit-gpt-oss-20b-core-grid",
            "description": "gpt-oss-20b on the 14 core benchmarks shared with pythia-6.9b and vicuna-7b-v1.3, enabling cross-model comparison beyond the classic HELM corpus.",
            "run_entries": [
                "boolq:model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=LGBTQ,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=all,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=black,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=christian,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=female,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=male,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=muslim,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=other_religions,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=white,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "entity_data_imputation:dataset=Buy,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "entity_data_imputation:dataset=Restaurant,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "entity_matching:dataset=Abt_Buy,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "entity_matching:dataset=Beer,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "entity_matching:dataset=Dirty_iTunes_Amazon,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "gsm:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "imdb:model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "lsat_qa:task=all,method=multiple_choice_joint,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu:subject=abstract_algebra,method=multiple_choice_joint,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu:subject=college_chemistry,method=multiple_choice_joint,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu:subject=computer_security,method=multiple_choice_joint,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu:subject=econometrics,method=multiple_choice_joint,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "narrative_qa:model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "quac:model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "synthetic_reasoning:mode=induction,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "synthetic_reasoning:mode=pattern_match,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "synthetic_reasoning:mode=variable_substitution,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "synthetic_reasoning_natural:difficulty=easy,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "synthetic_reasoning_natural:difficulty=hard,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "truthful_qa:task=mc_single,method=multiple_choice_joint,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=author,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=currency,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=discoverer_or_inventor,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=instance_of,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=medical_condition_treated,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=part_of,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=place_of_birth,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=plaintiff,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=position_held,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=symptoms_and_signs,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
            ],
            "suite": "audit-gpt-oss-20b-core-grid",
            "max_eval_instances": 1000,
            "devices": "0,1,2,3",
            "tmux_workers": 4,
        },
    },
    "gpt_oss_20b_vllm": {
        "profile": "gpt-oss-20b-completions",
        "bundle_name": "gpt_oss_20b_vllm",
        "access_kind": "openai-compatible",
        # G2: the old `-completions` profile name encoded this; now explicit.
        # (Frozen/archival preset — no catalog entry ships for it.)
        "protocol_mode": "completions",
        "model_deployment_name": "litellm/gpt-oss-20b-local",
        "smoke_manifest": {
            "experiment_name": "audit-gpt-oss-20b-vllm-smoke",
            "description": "Smoke-test HELM batch for openai/gpt-oss-20b through the local LiteLLM-backed vLLM service.",
            "run_entries": [
                "ifeval:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
            ],
            "suite": "audit-gpt-oss-20b-vllm-smoke",
            "max_eval_instances": 5,
        },
        "full_manifest": {
            "experiment_name": "audit-historic-grid-gpt-oss-20b-vllm-trimmed",
            "description": "Targeted in-scope historic-grid extension for openai/gpt-oss-20b using the local LiteLLM-backed vLLM service.",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "ifeval:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu_pro:subject=all,use_chain_of_thought=true,use_few_shot=false,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
            ],
            "suite": "audit-historic-grid-gpt-oss-20b-vllm-trimmed",
            "max_eval_instances": 1000,
        },
    },
    "qwen2_72b_vllm": {
        "profile": "qwen2-72b-instruct-tp2-balanced",
        "bundle_name": "qwen2_72b_vllm",
        "backend": "compose",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/qwen2-72b-instruct-local",
        # Instruct/chat model (official together/qwen2-72b-instruct is a
        # TogetherChatClient) → chat protocol.
        "protocol_mode": "chat",
        "smoke_manifest": {
            "experiment_name": "audit-qwen2-72b-vllm-smoke",
            "description": "Smoke-test HELM batch for qwen/qwen2-72b-instruct through a local vLLM server.",
            "run_entries": [
                "ewok:domain=agent_properties,model=qwen/qwen2-72b-instruct",
            ],
            "suite": "audit-qwen2-72b-vllm-smoke",
            "max_eval_instances": 5,
        },
        "full_manifest": {
            "experiment_name": "audit-historic-grid-qwen2-72b-vllm",
            "description": "Historic-grid reproduction batch for qwen/qwen2-72b-instruct through a local vLLM server.",
            "run_entries": [
                "ewok:domain=agent_properties,model=qwen/qwen2-72b-instruct",
                "ewok:domain=material_dynamics,model=qwen/qwen2-72b-instruct",
                "ewok:domain=material_properties,model=qwen/qwen2-72b-instruct",
                "ewok:domain=physical_dynamics,model=qwen/qwen2-72b-instruct",
                "ewok:domain=physical_interactions,model=qwen/qwen2-72b-instruct",
                "ewok:domain=physical_relations,model=qwen/qwen2-72b-instruct",
                "ewok:domain=quantitative_properties,model=qwen/qwen2-72b-instruct",
                "ewok:domain=social_interactions,model=qwen/qwen2-72b-instruct",
                "ewok:domain=social_properties,model=qwen/qwen2-72b-instruct",
                "ewok:domain=social_relations,model=qwen/qwen2-72b-instruct",
                "ewok:domain=spatial_relations,model=qwen/qwen2-72b-instruct",
            ],
            "suite": "audit-historic-grid-qwen2-72b-vllm",
            "max_eval_instances": 1000,
        },
    },
    "small_models_kubeai_overnight": {
        "bundle_name": "small_models_kubeai_overnight",
        "backend": "kubeai",
        "profiles": [
            {
                "profile": "qwen2-5-7b-instruct-turbo-default",
                "model_deployment_name": "kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "helm_model_name": "qwen/qwen2.5-7b-instruct-turbo",
                "helm_tokenizer_name": "qwen/qwen2.5-7b-instruct",
                # Instruct/chat model (official together/qwen2.5-7b-instruct-turbo
                # is a TogetherChatClient) → chat protocol.
                "protocol_mode": "chat",
            },
            {
                "profile": "vicuna-7b-v1-3-no-chat-template",
                "model_deployment_name": "kubeai/vicuna-7b-v1-3-no-chat-template-local",
                "helm_model_name": "lmsys/vicuna-7b-v1.3",
                "helm_tokenizer_name": "hf-internal-testing/llama-tokenizer",
                # Deliberately served WITHOUT a chat template (the deployment and
                # profile name say so; the official huggingface/vicuna-7b-v1.3 uses
                # the llama tokenizer, which has no chat template → completion
                # behavior) → completions protocol.
                "protocol_mode": "completions",
                # Keep a small headroom margin for the live vLLM/Vicuna path, which
                # appears to need a few reserved tokens beyond HELM's nominal budget.
                "helm_max_sequence_and_generated_tokens_length": 2040,
            },
        ],
        "smoke_manifest": {
            "experiment_name": "audit-small-models-kubeai-smoke",
            "description": "Smoke-test batch for the small KubeAI-served Qwen 2.5 7B and Vicuna 7B profiles.",
            "run_entries": [
                "ifeval:model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "boolq:model=lmsys/vicuna-7b-v1.3,data_augmentation=canonical,model_deployment=kubeai/vicuna-7b-v1-3-no-chat-template-local",
            ],
            "suite": "audit-small-models-kubeai-smoke",
            "max_eval_instances": 5,
        },
        "full_manifest": {
            "experiment_name": "audit-small-models-kubeai-overnight",
            "description": "Targeted overnight batch for the KubeAI-served Qwen 2.5 7B and Vicuna 7B profiles.",
            "run_entries": [
                "commonsense:dataset=openbookqa,method=multiple_choice_joint,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "gsm:model=qwen/qwen2.5-7b-instruct-turbo,stop=none,model_deployment=kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "med_qa:model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "narrative_qa:model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "boolq:model=lmsys/vicuna-7b-v1.3,data_augmentation=canonical,model_deployment=kubeai/vicuna-7b-v1-3-no-chat-template-local",
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=lmsys/vicuna-7b-v1.3,data_augmentation=canonical,model_deployment=kubeai/vicuna-7b-v1-3-no-chat-template-local",
                "narrative_qa:model=lmsys/vicuna-7b-v1.3,data_augmentation=canonical,model_deployment=kubeai/vicuna-7b-v1-3-no-chat-template-local",
            ],
            "suite": "audit-small-models-kubeai-overnight",
            "max_eval_instances": 1000,
        },
    },
    "finish_qwen25_gptoss": {
        # Closes the Qwen-2.5 + gpt-oss gaps surfaced by the Case Study 3
        # audit (see ``paper_draft/case_study_3_appendix.tex``):
        #   - Qwen 2.5 7B Instruct: 9 unique HELM run_entries from
        #     lite/v1.9.0 with no local repro at all (math × 7 subjects
        #     + natural_qa × 2 modes), plus reruns of the 6
        #     execution-spec-drifted benchmark families with the public
        #     adapter_spec.instructions intact.
        #   - gpt-oss 20B: 8 capabilities/v1.12.0 + safety/v1.14.0
        #     run_entries with no local repro.
        # Driven by the ``pythia-qwen25-gptoss-mixed-4x96`` profile in
        # the infer_stack submodule, which co-resides Qwen 2.5 + gpt-oss
        # alongside the two Pythia services another experiment uses on
        # the same host.
        "bundle_name": "finish_qwen25_gptoss",
        "backend": "compose",
        "infer_stack_profile": "pythia-qwen25-gptoss-mixed-4x96",
        "profiles": [
            {
                "profile": "qwen2-5-7b-instruct-turbo-default",
                "model_deployment_name": "vllm/qwen2-5-7b-instruct-turbo-local",
                "helm_model_name": "qwen/qwen2.5-7b-instruct-turbo",
                "helm_tokenizer_name": "qwen/qwen2.5-7b-instruct",
                # Instruct/chat model (official together/qwen2.5-7b-instruct-turbo
                # is a TogetherChatClient) → chat protocol.
                "protocol_mode": "chat",
            },
            {
                "profile": "gpt-oss-20b-chat",
                "model_deployment_name": "litellm/gpt-oss-20b-local",
                "helm_model_name": "openai/gpt-oss-20b",
                "helm_tokenizer_name": "openai/o200k_harmony",
                # gpt-oss needs the harmony format (applied via the chat template);
                # the official together/gpt-oss-20b is a TogetherChatClient → chat
                # protocol. (The frozen gpt_oss_20b_* presets pin completions; this
                # active preset matches the official deployment instead.)
                "protocol_mode": "chat",
            },
        ],
        "smoke_manifest": {
            "experiment_name": "audit-finish-qwen25-gptoss-smoke",
            "description": (
                "Smoke-test batch covering one Qwen 2.5 + one gpt-oss "
                "run_entry from the finish_qwen25_gptoss target list."
            ),
            "run_entries": [
                # One quick run from each model, both 5 instances.
                # Qwen smoke uses MMLU instead of MATH because the
                # MATH benchmark depends on the ``hendrycks/competition_math``
                # HF dataset which has been disabled in this preset
                # (see ``full_manifest.run_entries`` below for the
                # explanation).
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                "ifeval:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
            ],
            "suite": "audit-finish-qwen25-gptoss-smoke",
            "max_eval_instances": 5,
        },
        "full_manifest": {
            "experiment_name": "audit-finish-qwen25-gptoss",
            "description": (
                "Closes the Qwen 2.5 + gpt-oss gaps in the open-weight "
                "HELM audit (Case Study 3). Qwen rows replay the "
                "execution-spec-drifted public run_specs (with the "
                "matching adapter_spec.instructions prefix); MATH and "
                "natural_qa are disabled (data-access blockers — see "
                "notes); gpt-oss rows cover the 8 capabilities/safety "
                "entries from suite v1.12.0 / v1.14.0 with no local "
                "repro yet."
            ),
            "run_entries": [
                # ── Qwen 2.5 7B: missing benchmarks (no local repro yet)
                #
                # Two benchmark families are **disabled** in this preset
                # because their underlying datasets aren't reachable
                # cleanly from aiq-gpu today:
                #
                # 1. ``math:`` × 7 subjects (algebra,
                #    counting_and_probability, geometry,
                #    intermediate_algebra, number_theory, prealgebra,
                #    precalculus) at level=1, CoT=True. Loads the
                #    ``hendrycks/competition_math`` HuggingFace dataset.
                # 2. ``natural_qa:`` × 2 modes (closedbook,
                #    openbook_longans). HELM fetches the natural_questions
                #    dataset from a Google Storage URL that returns
                #    HTTP 403 from aiq-gpu (gated / pulled / blocked
                #    egress — observed 2026-04-30).
                #
                # Re-enable each by un-commenting its run_entries below
                # AND restoring the matching dataset name in
                # ``02_warmup_data.sh``.

                # ── Qwen 2.5 7B: rerun execution-spec-drifted families
                # The local audit previously ran these without the
                # public adapter_spec.instructions prefix that the
                # public HELM Qwen runs use; rerunning here pulls the
                # public run_spec via eval-audit-run, which carries the
                # prefix through to the locally-served model.
                # MMLU × 10 subjects (one entry per subject; HELM run
                # naming uses subject as the only varying knob for
                # mmlu's recipe-canonical packets).
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                # legalbench × 10 subjects
                "legalbench:subset=abercrombie,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                # commonsense × 2
                "commonsense:dataset=openbookqa,method=multiple_choice_joint,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                # gsm — was completion_content_drift; rerun with public
                # adapter_spec to verify the stop-sequence handling
                # matches now.
                "gsm:model=qwen/qwen2.5-7b-instruct-turbo,stop=none,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                # med_qa
                "med_qa:model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                # narrative_qa
                "narrative_qa:model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                # wmt_14 × 10 language pairs (one representative; the
                # rest follow the same pattern and can be added as
                # HELM_EXTRA_RUN_ENTRIES if desired)
                "wmt_14:language_pair=fr-en,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",

                # ── gpt-oss 20B: missing capabilities/v1.12.0 entries
                # gpqa is a gated HF dataset (Idavidrein/gpqa) and
                # cannot be downloaded from aiq-gpu without HF
                # credentials with access to the gate. Disabled
                # 2026-04-30; re-enable by restoring this line and
                # adding ``Idavidrein/gpqa`` back to
                # 02_warmup_data.sh once credentials are in place.
                # "gpqa:subset=gpqa_main,use_chain_of_thought=true,use_few_shot=false,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu_pro:subject=all,use_chain_of_thought=true,use_few_shot=false,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                # Disabled 2026-04-30: external-validator scenarios.
                # These benchmarks ship HELM annotators that hardcode
                # third-party graders (openai/gpt-4o, together/llama-...)
                # via ``Annotator.auto_client.make_request(...)``. The
                # graders pull credentials from HELM's
                # ``prod_env/credentials.conf`` (or ``$HELM_CREDENTIALS``)
                # — *not* from our bundle's ``model_deployments.yaml``.
                # Since this is a *local* reproducibility audit, we don't
                # send queries to external paid APIs. Re-enable by
                # uncommenting and either dropping a ``credentials.conf``
                # at the helm-run base-path or exporting
                # ``HELM_CREDENTIALS='openaiApiKey: "sk-..."'``.
                # "omni_math:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",  # OmniMATHAnnotator (LLM-as-jury)
                # "wildbench:subset=v2,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",  # WildBenchAnnotator → openai/gpt-4o + together/llama-3.1-405b
                # "anthropic_red_team:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",  # AnthropicRedTeamAnnotator
                # "harm_bench:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",  # HarmBenchAnnotator (LLM-as-jury)
                # "simple_safety_tests:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",  # SimpleSafetyTestsAnnotator
                # "xstest:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",  # XSTestAnnotator
            ],
            "suite": "audit-finish-qwen25-gptoss",
            "max_eval_instances": 1000,
        },
    },
    "e2e-phi_2-vllm-philosophy": {
        "profile": "phi2-single",
        "bundle_name": "e2e-phi_2-vllm",
        "access_kind": "openai-compatible",
        "model_deployment_name": "vllm/phi-2-local",
        "profiles": [
            {
                "profile": "phi2-single",
                # The bundle keeps its NATIVE local deployment name on both paths.
                # Under --from-spec the generated manifest's `model_deployment`
                # field carries this same name (set by the exporter), and the
                # from-spec CLI rewrites the replayed run_spec.json's
                # adapter_spec.model_deployment to it — so the produced run records
                # the LOCAL endpoint and the audit reports same_deployment=no.
                # Rewrite target == registered name by construction (no drift).
                # Supersedes the by-name `from_spec_model_deployment_name` rekey;
                # see docs/planning/from-spec-deployment-rewrite-plan.md Change 5.
                "model_deployment_name": "vllm/phi-2-local",
                "helm_model_name": "microsoft/phi-2",
                "helm_tokenizer_name": "microsoft/phi-2",
                "protocol_mode": "completions",
                "helm_max_sequence_and_generated_tokens_length": 2048,
            }
        ],
        "smoke_manifest": {
            "experiment_name": "e2e-phi_2-vllm-philosophy-smoke",
            "description": "Smoke-test for phi-2 on a small HELM batch.",
            "run_entries": [
                "mmlu:subject=philosophy,method=multiple_choice_joint,model=microsoft/phi-2,eval_split=test"
            ],
            "suite": "e2e-phi_2-vllm-philosophy-smoke",
            "max_eval_instances": 5,
            # From-spec recipe SOURCE (migration plan Change 2a). Only emitted into
            # the generated manifest when export-benchmark-bundle --from-spec is
            # passed; narrowed to the mmlu subtree so discovery is fast/unambiguous.
            "precomputed_root": "/data/crfm-helm-public/mmlu",
            # Containerized HELM is mandatory (the docker pipeline pins the
            # software env). network=host so the in-container HELM client reaches
            # the host-served model (vLLM behind LiteLLM); gpus=none keeps the
            # client off the serving GPUs (it's an HTTP caller; the model's GPU is
            # leased separately). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "e2e-phi_2-vllm-philosophy-full",
            "description": "Full HELM batch for phi-2.",
            "run_entries": [
                "mmlu:subject=philosophy,method=multiple_choice_joint,model=microsoft/phi-2,eval_split=test"
            ],
            "suite": "e2e-phi_2-vllm-philosophy-full",
            "max_eval_instances": 1000,
            # From-spec recipe SOURCE (migration plan Change 2a). The official
            # adapter_spec.max_eval_instances is 10000, so this 1000 cap compares on
            # HELM's deterministic instance PREFIX, not official parity (plan §7).
            "precomputed_root": "/data/crfm-helm-public/mmlu",
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    # NOTE: the incomparable negative control deliberately carries NO from-spec
    # fields. From-spec replays the official recipe verbatim, which would erase the
    # temperature=1 deviation this scenario exists to flag, so it stays on the
    # run-entry path (migration plan Change 4 / §7). The grid's e2e_fromspec_enabled
    # also excludes it, so --from-spec is never passed for it.
    "e2e-phi_2-vllm-philosophy-incomparable": {
        "profile": "phi2-single",
        "bundle_name": "e2e-phi_2-vllm-philosophy-incomparable",
        "access_kind": "openai-compatible",
        "model_deployment_name": "vllm/phi-2-local",
        "profiles": [
            {
                "profile": "phi2-single",
                "model_deployment_name": "vllm/phi-2-local",
                "helm_model_name": "microsoft/phi-2",
                "helm_tokenizer_name": "microsoft/phi-2",
                "protocol_mode": "completions",
                "helm_max_sequence_and_generated_tokens_length": 2048,
            }
        ],
        "smoke_manifest": {
            "experiment_name": "e2e-phi_2-vllm-philosophy-incomparable-smoke",
            "description": "Smoke-test for phi-2 with temperature changed",
            "run_entries": [
                "mmlu:subject=philosophy,method=multiple_choice_joint,model=microsoft/phi-2,eval_split=test,temperature=1"
            ],
            "suite": "e2e-phi_2-vllm-philosophy-incomparable-smoke",
            "max_eval_instances": 5,
            # Containerized HELM (mandatory); see e2e-phi_2-vllm-philosophy.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "e2e-phi_2-vllm-philosophy-incomparable-full",
            "description": "Full HELM batch for phi-2 with temperature changed",
            "run_entries": [
                'mmlu:subject=philosophy,method=multiple_choice_joint,model=microsoft/phi-2,eval_split=test,temperature=1'
            ],
            "suite": "e2e-phi_2-vllm-philosophy-incomparable-full",
            "max_eval_instances": 1000,
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    # NOTE: the former ``e2e-phi_2-vllm-philosophy-container`` preset was removed
    # once containerization became mandatory for every run — it had become an
    # exact duplicate of ``e2e-phi_2-vllm-philosophy`` (whose smoke/full manifests
    # now carry container_network: host + hf_cache_dir, so it runs in-container
    # too). The image is supplied per-run via ``eval-audit-run --container-image``.
    # allenai/olmo-7b is reproduced under TWO official HELM suites, split into
    # two experiments so each from-spec discovery key resolves to exactly one
    # official run (migration plan §4.1 + the olmo-7b suite-split decision):
    #   -mmlu: the full MMLU suite (eval_split=test, /mmlu v1.1.0)
    #   -lite: HELM-Lite (commonsense/gsm/legalbench/med_qa/narrative_qa/wmt_14
    #          + the 5 curated HELM-Lite MMLU subjects, /lite v1.2.0).
    # The per-subject mmlu runs exist in BOTH suites (lite's dir name is a
    # token-subset of mmlu's), so a shared root would be ambiguous; per-suite
    # precomputed_root is what disambiguates.
    "allenai-olmo-7b-mmlu": {
        "profile": "allenai-olmo-7b-single",
        "bundle_name": "allenai-olmo-7b-mmlu",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmo-7b",
        # G1: HELM model/tokenizer aliases (moved out of the deleted infer_stack
        # models.yaml). Base model → completions protocol (G2).
        "helm_model_name": "allenai/olmo-7b",
        "helm_tokenizer_name": "allenai/olmo-7b",
        "protocol_mode": "completions",
        # Reserve 32 tokens below max-model-len (2048) so the live vLLM path
        # never trips its hard prompt+generation budget: HELM truncates the
        # *raw* prompt to this budget, but the served client adds a few tokens
        # (BOS / chat-template wrapper) HELM can't see. Without the reserve,
        # num_output_tokens-heavy run_entries overflow by ~1-13 tokens and vLLM
        # returns ContextWindowExceededError. Keep <= models.yaml max_model_len.
        "helm_max_sequence_and_generated_tokens_length": 2016,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmo-7b-mmlu-smoke",
            "description": "Smoke batch for allenai/olmo-7b (mmlu suite); from-spec replay of the official run_spec.json.",
            "run_entries": [
                "mmlu:subject=abstract_algebra,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
            ],
            "suite": "audit-allenai-olmo-7b-mmlu-smoke",
            "max_eval_instances": 5,
            # From-spec recipe SOURCE (migration plan Change 1): the official
            # run_spec.json is discovered under this root. mmlu
            # suite only, so the per-subject mmlu runs that exist in BOTH suites
            # resolve unambiguously (the other suite's dir is not under this root).
            "precomputed_root": "/data/crfm-helm-public/mmlu",
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container HELM client reaches the
            # host-served model (vLLM behind LiteLLM); gpus=none keeps HELM off
            # the serving GPUs (MC/exact-match entries, no local-HF judge).
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmo-7b-mmlu-full",
            "description": "Full batch for allenai/olmo-7b (mmlu suite); from-spec replay of the official run_spec.json.",
            "run_entries": [
                "mmlu:subject=abstract_algebra,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=anatomy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=astronomy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=business_ethics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=clinical_knowledge,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=college_biology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=college_chemistry,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=college_computer_science,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=college_mathematics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=college_medicine,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=college_physics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=computer_security,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=conceptual_physics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=econometrics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=electrical_engineering,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=elementary_mathematics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=formal_logic,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=global_facts,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_biology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_chemistry,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_computer_science,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_european_history,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_geography,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_government_and_politics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_macroeconomics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_mathematics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_microeconomics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_physics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_psychology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_statistics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_us_history,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_world_history,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=human_aging,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=human_sexuality,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=international_law,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=jurisprudence,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=logical_fallacies,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=machine_learning,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=management,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=marketing,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=medical_genetics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=miscellaneous,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=moral_disputes,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=moral_scenarios,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=nutrition,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=philosophy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=prehistory,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=professional_accounting,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=professional_law,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=professional_medicine,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=professional_psychology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=public_relations,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=security_studies,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=sociology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=virology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=world_religions,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
            ],
            "suite": "audit-allenai-olmo-7b-mmlu-full",
            "max_eval_instances": 1000,
            # From-spec recipe SOURCE (migration plan Change 1): the official
            # run_spec.json is discovered under this root. mmlu
            # suite only, so the per-subject mmlu runs that exist in BOTH suites
            # resolve unambiguously (the other suite's dir is not under this root).
            "precomputed_root": "/data/crfm-helm-public/mmlu",
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container HELM client reaches the
            # host-served model (vLLM behind LiteLLM); gpus=none keeps HELM off
            # the serving GPUs (MC/exact-match entries, no local-HF judge).
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    "allenai-olmo-7b-lite": {
        "profile": "allenai-olmo-7b-single",
        "bundle_name": "allenai-olmo-7b-lite",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmo-7b",
        # G1: HELM model/tokenizer aliases (moved out of the deleted infer_stack
        # models.yaml). Base model → completions protocol (G2).
        "helm_model_name": "allenai/olmo-7b",
        "helm_tokenizer_name": "allenai/olmo-7b",
        "protocol_mode": "completions",
        # Reserve 32 tokens below max-model-len (2048) so the live vLLM path
        # never trips its hard prompt+generation budget: HELM truncates the
        # *raw* prompt to this budget, but the served client adds a few tokens
        # (BOS / chat-template wrapper) HELM can't see. Without the reserve,
        # num_output_tokens-heavy run_entries overflow by ~1-13 tokens and vLLM
        # returns ContextWindowExceededError. Keep <= models.yaml max_model_len.
        "helm_max_sequence_and_generated_tokens_length": 2016,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmo-7b-lite-smoke",
            "description": "Smoke batch for allenai/olmo-7b (lite suite); from-spec replay of the official run_spec.json.",
            "run_entries": [
                "commonsense:method=multiple_choice_joint,dataset=openbookqa,model=allenai/olmo-7b",
                # Canary (promoted from full_manifest): wmt_14 loads a HF dataset
                # whose id resolution is huggingface_hub-version sensitive (the
                # "wmt-14 isn't a valid HF dataset" failure) and scores via sacrebleu
                # from crfm-helm[metrics]. Keeping it in the SMOKE set makes a stale
                # or mis-built ([heim]-only / floated-hub) runner image fail this
                # cheap preflight instead of deep in the full grid.
                "wmt_14:language_pair=fr-en,model=allenai/olmo-7b",
            ],
            "suite": "audit-allenai-olmo-7b-lite-smoke",
            "max_eval_instances": 5,
            # From-spec recipe SOURCE (migration plan Change 1): the official
            # run_spec.json is discovered under this root. lite
            # suite only, so the per-subject mmlu runs that exist in BOTH suites
            # resolve unambiguously (the other suite's dir is not under this root).
            "precomputed_root": "/data/crfm-helm-public/lite",
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container HELM client reaches the
            # host-served model (vLLM behind LiteLLM); gpus=none keeps HELM off
            # the serving GPUs (MC/exact-match entries, no local-HF judge).
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmo-7b-lite-full",
            "description": "Full batch for allenai/olmo-7b (lite suite); from-spec replay of the official run_spec.json.",
            "run_entries": [
                "commonsense:method=multiple_choice_joint,dataset=openbookqa,model=allenai/olmo-7b",
                "gsm:model=allenai/olmo-7b",
                "legalbench:subset=abercrombie,model=allenai/olmo-7b",
                "legalbench:subset=corporate_lobbying,model=allenai/olmo-7b",
                "legalbench:subset=function_of_decision_section,model=allenai/olmo-7b",
                "legalbench:subset=international_citizenship_questions,model=allenai/olmo-7b",
                "legalbench:subset=proa,model=allenai/olmo-7b",
                "med_qa:model=allenai/olmo-7b",
                "mmlu:subject=abstract_algebra,method=multiple_choice_joint,model=allenai/olmo-7b",
                "mmlu:subject=college_chemistry,method=multiple_choice_joint,model=allenai/olmo-7b",
                "mmlu:subject=computer_security,method=multiple_choice_joint,model=allenai/olmo-7b",
                "mmlu:subject=econometrics,method=multiple_choice_joint,model=allenai/olmo-7b",
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=allenai/olmo-7b",
                "narrative_qa:model=allenai/olmo-7b",
                "wmt_14:language_pair=cs-en,model=allenai/olmo-7b",
                "wmt_14:language_pair=de-en,model=allenai/olmo-7b",
                "wmt_14:language_pair=fr-en,model=allenai/olmo-7b",
                "wmt_14:language_pair=hi-en,model=allenai/olmo-7b",
                "wmt_14:language_pair=ru-en,model=allenai/olmo-7b",
            ],
            "suite": "audit-allenai-olmo-7b-lite-full",
            "max_eval_instances": 1000,
            # From-spec recipe SOURCE (migration plan Change 1): the official
            # run_spec.json is discovered under this root. lite
            # suite only, so the per-subject mmlu runs that exist in BOTH suites
            # resolve unambiguously (the other suite's dir is not under this root).
            "precomputed_root": "/data/crfm-helm-public/lite",
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container HELM client reaches the
            # host-served model (vLLM behind LiteLLM); gpus=none keeps HELM off
            # the serving GPUs (MC/exact-match entries, no local-HF judge).
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    "allenai-olmo-2-0325-32b-instruct": {
        "profile": "allenai-olmo-2-0325-32b-instruct-single",
        "bundle_name": "allenai-olmo-2-0325-32b-instruct",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmo-2-0325-32b-instruct",
        # G1: HELM aliases. Instruct model → chat protocol (G2, explicit).
        "helm_model_name": "allenai/olmo-2-0325-32b-instruct",
        "helm_tokenizer_name": "allenai/olmo-2-0325-32b-instruct",
        "protocol_mode": "chat",
        # 32-token reserve below max-model-len (4096); see allenai-olmo-7b.
        # The chat-template wrapper adds ~12 tokens HELM doesn't count, so the
        # num_output_tokens=2048 run_entries (gpqa/mmlu_pro/ifeval) overflow
        # 4096 without it.
        "helm_max_sequence_and_generated_tokens_length": 4064,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmo-2-0325-32b-instruct-smoke",
            "description": "Smoke-test batch for allenai/olmo-2-0325-32b-instruct; run_entries from candidate_runs.json.",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmo-2-0325-32b-instruct",
            ],
            "suite": "audit-allenai-olmo-2-0325-32b-instruct-smoke",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 5,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmo-2-0325-32b-instruct-full",
            "description": "Full local-reproduction batch for allenai/olmo-2-0325-32b-instruct; run_entries sourced from candidate_runs.json. Includes 1 gated-dataset run(s) (require HuggingFace auth).",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmo-2-0325-32b-instruct",
                "gpqa:subset=gpqa_main,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-0325-32b-instruct",
                "ifeval:num_output_tokens=2048,model=allenai/olmo-2-0325-32b-instruct",
                "mmlu_pro:subject=all,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-0325-32b-instruct",
            ],
            "suite": "audit-allenai-olmo-2-0325-32b-instruct-full",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 1000,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    "allenai-olmo-1-7-7b": {
        "profile": "allenai-olmo-1-7-7b-single",
        "bundle_name": "allenai-olmo-1-7-7b",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmo-1-7-7b",
        # G1: tokenizer alias is case-sensitive and non-obvious (the "-hf"
        # conversion repo), distinct from the lowercase HELM model alias.
        # Base model → completions protocol (G2).
        "helm_model_name": "allenai/olmo-1.7-7b",
        "helm_tokenizer_name": "allenai/OLMo-1.7-7B-hf",
        "protocol_mode": "completions",
        # 32-token reserve below max-model-len (4096); see allenai-olmo-7b.
        "helm_max_sequence_and_generated_tokens_length": 4064,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmo-1-7-7b-smoke",
            "description": "Smoke-test batch for allenai/olmo-1.7-7b; run_entries from candidate_runs.json.",
            "run_entries": [
                "mmlu:subject=abstract_algebra,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
            ],
            "suite": "audit-allenai-olmo-1-7-7b-smoke",
            "precomputed_root": "/data/crfm-helm-public/mmlu",
            "max_eval_instances": 5,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmo-1-7-7b-full",
            "description": "Full local-reproduction batch for allenai/olmo-1.7-7b; run_entries sourced from candidate_runs.json.",
            "run_entries": [
                "mmlu:subject=abstract_algebra,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=anatomy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=astronomy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=business_ethics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=clinical_knowledge,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=college_biology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=college_chemistry,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=college_computer_science,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=college_mathematics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=college_medicine,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=college_physics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=computer_security,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=conceptual_physics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=econometrics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=electrical_engineering,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=elementary_mathematics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=formal_logic,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=global_facts,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_biology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_chemistry,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_computer_science,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_european_history,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_geography,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_government_and_politics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_macroeconomics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_mathematics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_microeconomics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_physics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_psychology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_statistics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_us_history,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_world_history,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=human_aging,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=human_sexuality,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=international_law,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=jurisprudence,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=logical_fallacies,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=machine_learning,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=management,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=marketing,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=medical_genetics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=miscellaneous,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=moral_disputes,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=moral_scenarios,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=nutrition,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=philosophy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=prehistory,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=professional_accounting,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=professional_law,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=professional_medicine,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=professional_psychology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=public_relations,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=security_studies,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=sociology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=virology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=world_religions,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
            ],
            "suite": "audit-allenai-olmo-1-7-7b-full",
            "precomputed_root": "/data/crfm-helm-public/mmlu",
            "max_eval_instances": 1000,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    "allenai-olmo-2-1124-13b-instruct": {
        "profile": "allenai-olmo-2-1124-13b-instruct-single",
        "bundle_name": "allenai-olmo-2-1124-13b-instruct",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmo-2-1124-13b-instruct",
        # G1: the 13B reuses the 7B tokenizer alias (intentional, not a typo).
        # Instruct model → chat protocol (G2, explicit).
        "helm_model_name": "allenai/olmo-2-1124-13b-instruct",
        "helm_tokenizer_name": "allenai/olmo-2-1124-7b-instruct",
        "protocol_mode": "chat",
        # 32-token reserve below max-model-len (4096); see allenai-olmo-7b.
        "helm_max_sequence_and_generated_tokens_length": 4064,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmo-2-1124-13b-instruct-smoke",
            "description": "Smoke-test batch for allenai/olmo-2-1124-13b-instruct; run_entries from candidate_runs.json.",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmo-2-1124-13b-instruct",
            ],
            "suite": "audit-allenai-olmo-2-1124-13b-instruct-smoke",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 5,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmo-2-1124-13b-instruct-full",
            "description": "Full local-reproduction batch for allenai/olmo-2-1124-13b-instruct; run_entries sourced from candidate_runs.json. Includes 1 gated-dataset run(s) (require HuggingFace auth).",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmo-2-1124-13b-instruct",
                "gpqa:subset=gpqa_main,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-1124-13b-instruct",
                "ifeval:num_output_tokens=2048,model=allenai/olmo-2-1124-13b-instruct",
                "mmlu_pro:subject=all,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-1124-13b-instruct",
            ],
            "suite": "audit-allenai-olmo-2-1124-13b-instruct-full",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 1000,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    "allenai-olmo-2-1124-7b-instruct": {
        "profile": "allenai-olmo-2-1124-7b-instruct-single",
        "bundle_name": "allenai-olmo-2-1124-7b-instruct",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmo-2-1124-7b-instruct",
        # G1: HELM aliases. Instruct model → chat protocol (G2, explicit).
        "helm_model_name": "allenai/olmo-2-1124-7b-instruct",
        "helm_tokenizer_name": "allenai/olmo-2-1124-7b-instruct",
        "protocol_mode": "chat",
        # 32-token reserve below max-model-len (4096); see allenai-olmo-7b.
        "helm_max_sequence_and_generated_tokens_length": 4064,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmo-2-1124-7b-instruct-smoke",
            "description": "Smoke-test batch for allenai/olmo-2-1124-7b-instruct; run_entries from candidate_runs.json.",
            "run_entries": [
                "gpqa:subset=gpqa_main,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-1124-7b-instruct",
                # Canary (promoted from full_manifest): ifeval's metric imports
                # langdetect, which lives only in crfm-helm[metrics]/[cleva] — a
                # [heim]-built runner image dies here with "ModuleNotFoundError:
                # langdetect". One ifeval entry in the SMOKE set catches a
                # mis-built/stale image on this cheap preflight, not the full grid.
                # (The container env is shared across models, so a single instruct
                # canary suffices for the whole grid.)
                "ifeval:num_output_tokens=2048,model=allenai/olmo-2-1124-7b-instruct",
            ],
            "suite": "audit-allenai-olmo-2-1124-7b-instruct-smoke",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 5,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmo-2-1124-7b-instruct-full",
            "description": "Full local-reproduction batch for allenai/olmo-2-1124-7b-instruct; run_entries sourced from candidate_runs.json. Includes 1 gated-dataset run(s) (require HuggingFace auth).",
            "run_entries": [
                "gpqa:subset=gpqa_main,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-1124-7b-instruct",
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmo-2-1124-7b-instruct",
                "ifeval:num_output_tokens=2048,model=allenai/olmo-2-1124-7b-instruct",
                "mmlu_pro:subject=all,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-1124-7b-instruct",
            ],
            "suite": "audit-allenai-olmo-2-1124-7b-instruct-full",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 1000,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    "allenai-olmoe-1b-7b-0125-instruct": {
        "profile": "allenai-olmoe-1b-7b-0125-instruct-single",
        "bundle_name": "allenai-olmoe-1b-7b-0125-instruct",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmoe-1b-7b-0125-instruct",
        # G1: HELM aliases. Instruct model → chat protocol (G2, explicit).
        "helm_model_name": "allenai/olmoe-1b-7b-0125-instruct",
        "helm_tokenizer_name": "allenai/olmoe-1b-7b-0125-instruct",
        "protocol_mode": "chat",
        # 32-token reserve below max-model-len (4096); see allenai-olmo-7b.
        # OLMoE's chat template adds ~13 tokens HELM doesn't count.
        "helm_max_sequence_and_generated_tokens_length": 4064,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmoe-1b-7b-0125-instruct-smoke",
            "description": "Smoke-test batch for allenai/olmoe-1b-7b-0125-instruct; run_entries from candidate_runs.json.",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmoe-1b-7b-0125-instruct",
            ],
            "suite": "audit-allenai-olmoe-1b-7b-0125-instruct-smoke",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 5,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmoe-1b-7b-0125-instruct-full",
            "description": "Full local-reproduction batch for allenai/olmoe-1b-7b-0125-instruct; run_entries sourced from candidate_runs.json. Includes 1 gated-dataset run(s) (require HuggingFace auth).",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmoe-1b-7b-0125-instruct",
                "gpqa:subset=gpqa_main,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmoe-1b-7b-0125-instruct",
                "ifeval:num_output_tokens=2048,model=allenai/olmoe-1b-7b-0125-instruct",
                "mmlu_pro:subject=all,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmoe-1b-7b-0125-instruct",
            ],
            "suite": "audit-allenai-olmoe-1b-7b-0125-instruct-full",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 1000,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Combined multi-model OLMo preset (parent-root fan-out)
# ─────────────────────────────────────────────────────────────────────────────
# A single MULTI-deployment bundle spanning the five OLMo models whose official
# runs all resolve under the shared parent root ``/data/crfm-helm-public``
# (olmo-1.7-7b + the four OLMo-2 / OLMoE instruct models). It exists so the OLMo
# grid can fan out across GPUs *under one schedule*: export it with
# ``--freeze-rel-paths`` and run ``eval-audit-run --tmux-workers N`` — cmd_queue
# then issues N concurrent per-run leases and infer-stack co-hosts what fits /
# serializes the rest across the pool (olmo-multi-model-from-spec-plan.md §4.4,
# §4.7). The seven single-model presets above are LEFT INTACT; this one REUSES
# their profile facts and run_entries verbatim (one source of truth — no
# transcription drift) and only appends the inline ``model_deployment=<local>``
# token each frozen source needs as its per-run rewrite target + lease key.
#
# Why these five and not olmo-7b: the olmo-7b ``-mmlu`` / ``-lite`` presets share
# their per-subject MMLU run dirs across the ``/mmlu`` and ``/lite`` suites, so the
# broad parent root is AMBIGUOUS for them — they keep the narrow per-suite roots
# and stay single-model. olmo-1.7-7b's MMLU resolves 57/57 with 0 AMBIGUOUS under
# the parent root, so the five share one root cleanly (plan §4.4).
#
# REQUIRES the exact-path exporter (``--freeze-rel-paths``): a multi-deployment
# bundle has no single manifest-level rewrite target, so the plain ``--from-spec``
# *discovery* path can't place it. The freeze path strips each local token for
# discovery (``_strip_local_deployment`` — local-only) and reuses it as that run's
# rewrite target (``_freeze_run_spec_sources``). Grid wiring (a runbook target +
# ``--tmux-workers``) and the host preflight's matching local-strip are the
# remaining steps (plan §4.3, §4.7) — this preset is the exporter half.
_OLMO_COMBINED_PRESET_KEYS = (
    "allenai-olmo-1-7-7b",
    "allenai-olmo-2-1124-7b-instruct",
    "allenai-olmo-2-1124-13b-instruct",
    "allenai-olmoe-1b-7b-0125-instruct",
    "allenai-olmo-2-0325-32b-instruct",
)


def _inline_local_deployment(run_entries: list[str], deployment: str) -> list[str]:
    """Suffix each from-spec run-entry with an inline ``model_deployment=<local>``.

    A multi-deployment from-spec bundle can't infer which local server a run uses
    from a bare discovery key, so each entry names its LOCAL rewrite target inline
    (the run-entry multi-model convention). ``_freeze_run_spec_sources`` strips the
    token for discovery (``_strip_local_deployment`` — local-only) and reuses it as
    that source's per-run rewrite target and ``lease_endpoints`` key. See
    docs/planning/olmo-multi-model-from-spec-plan.md §4.4.
    """
    return [f"{entry},model_deployment={deployment}" for entry in run_entries]


def _olmo_combined_run_entries(mode: str) -> list[str]:
    """Union of the five parent-root presets' ``{mode}_manifest`` run_entries, each
    suffixed with its own inline local deployment token (same model order as the
    ``profiles`` list, so the frozen sources and ``lease_endpoints`` map line up)."""
    entries: list[str] = []
    for key in _OLMO_COMBINED_PRESET_KEYS:
        cfg = PRESET_CONFIGS[key]
        entries.extend(
            _inline_local_deployment(
                cfg[f"{mode}_manifest"]["run_entries"], cfg["model_deployment_name"]
            )
        )
    return entries


PRESET_CONFIGS["allenai-olmo-combined"] = {
    "bundle_name": "allenai-olmo-combined",
    # Native access is vLLM-direct like the singles; the grid overrides with
    # --access-kind openai-compatible (the LiteLLM gateway) at export time.
    "access_kind": "vllm-direct",
    # One profile per model, pulled from each single-model preset so the HELM
    # aliases / protocol mode / token-budget reserve stay defined in exactly one
    # place. Order is load-bearing: _lease_facts zips profiles <-> serving facts to
    # build the lease_endpoints {deployment: endpoint} map.
    "profiles": [
        {
            "profile": PRESET_CONFIGS[key]["profile"],
            "access_kind": PRESET_CONFIGS[key]["access_kind"],
            "model_deployment_name": PRESET_CONFIGS[key]["model_deployment_name"],
            "helm_model_name": PRESET_CONFIGS[key]["helm_model_name"],
            "helm_tokenizer_name": PRESET_CONFIGS[key]["helm_tokenizer_name"],
            "protocol_mode": PRESET_CONFIGS[key]["protocol_mode"],
            "helm_max_sequence_and_generated_tokens_length": PRESET_CONFIGS[key][
                "helm_max_sequence_and_generated_tokens_length"
            ],
        }
        for key in _OLMO_COMBINED_PRESET_KEYS
    ],
    "smoke_manifest": {
        "experiment_name": "audit-allenai-olmo-combined-smoke",
        "description": (
            "Smoke batch for the combined multi-model OLMo bundle: each model's "
            "own smoke run_entries (all five endpoints exercised, incl. the "
            "ifeval/langdetect container canary); from-spec exact-path replay under "
            "the shared parent root. Run with --tmux-workers N to fan the models "
            "across GPUs under one schedule."
        ),
        "run_entries": _olmo_combined_run_entries("smoke"),
        "suite": "audit-allenai-olmo-combined-smoke",
        # Shared parent root — every model's runs resolve here (0 AMBIGUOUS).
        "precomputed_root": "/data/crfm-helm-public",
        "max_eval_instances": 5,
        # Containerized HELM is mandatory (the grid passes --container-image); host
        # network so the in-container client reaches the host-served vLLM, gpus=none
        # keeps HELM off the serving GPUs (each model's GPU is leased separately).
        "container_network": "host",
        "hf_cache_dir": "~/.cache/eval-audit-hf",
        "container_gpus": "none",
    },
    "full_manifest": {
        "experiment_name": "audit-allenai-olmo-combined-full",
        "description": (
            "Full local-reproduction batch for the combined multi-model OLMo "
            "bundle: the union of the five parent-root presets' full run_entries "
            "(olmo-1.7-7b MMLU x 57 + the four instruct models' "
            "bbq/gpqa/ifeval/mmlu_pro), each replayed from its official "
            "run_spec.json via exact-path freeze. Includes gated-dataset runs "
            "(gpqa requires HuggingFace auth). Run with --tmux-workers N to fan "
            "across GPUs."
        ),
        "run_entries": _olmo_combined_run_entries("full"),
        "suite": "audit-allenai-olmo-combined-full",
        "precomputed_root": "/data/crfm-helm-public",
        "max_eval_instances": 1000,
        "container_network": "host",
        "hf_cache_dir": "~/.cache/eval-audit-hf",
        "container_gpus": "none",
    },
}


# The infer_stack leasing front door is a single LiteLLM gateway; its host port
# is a fixed default in the new CLI (config.py:DEFAULT_PORTS['litellm']). Callers
# normally pass an explicit --base-url resolved from `infer-stack env`, but when
# none is given we fall back to this deterministic gateway URL (default-B; see
# docs/planning/infer-stack-cli-api-migration.md §5.G3).
DEFAULT_GATEWAY_PORT = 14042
# The managed env-var holding the LiteLLM master key (infer_stack
# compose.py:API_KEY_ENV). Unchanged across the catalog/leasing rewrite.
LITELLM_AUTH_ENV = "LITELLM_MASTER_KEY"

# Default soft TTL baked into a materialized manifest's lease facts. Must exceed
# worst-case (model cold-load + run) so a hard-killed job's leaked lease is
# reclaimed by the admission queue's sweep / `infer-stack gc` rather than
# expiring mid-run (integration plan §8). Per-preset overrides via
# PRESET_CONFIGS[...]["lease_ttl"]; per-run overrides via `eval-audit-run
# --lease-ttl`. Kept in sync with helm_docker_pipeline._DEFAULT_LEASE_TTL.
DEFAULT_LEASE_TTL = "4h"


def infer_stack_root() -> Path:
    return repo_root() / "submodules" / "infer_stack"


def _ensure_importable_infer_stack(root: Path | None = None) -> None:
    package_root = str((root or infer_stack_root()).resolve())
    if package_root not in sys.path:
        sys.path.insert(0, package_root)


def _import_infer_stack_leasing(root: Path | None = None) -> Any:
    """Import the vendored ``infer_stack.leasing`` package (``Catalog`` et al.).

    Replaces the deleted ``infer_stack.contracts`` import. The catalog/leasing
    world is the new source of transport facts (served name, backing HF model,
    served context window)."""
    _ensure_importable_infer_stack(root)
    return importlib.import_module("infer_stack.leasing")


def _infer_stack_config_root(config_dir: Path | None = None) -> Path:
    """Resolve the infer-stack config dir that holds ``catalog.yaml``.

    Honors an explicit override (the CLI ``--config-dir`` / ``--vllm-root``),
    otherwise defers to infer_stack's own ``config_root()`` (which reads
    ``INFER_STACK_CONFIG_DIR``)."""
    if config_dir is not None:
        return Path(config_dir).expanduser().resolve()
    _ensure_importable_infer_stack()
    paths = importlib.import_module("infer_stack.paths")
    return paths.config_root()


@dataclass(frozen=True)
class ServingFacts:
    """The transport facts the serving catalog uniquely supplies for one endpoint.

    Everything HELM-domain (model/tokenizer alias, protocol mode) comes from the
    eval_audit preset; everything transport (base_url, api key, access kind) is
    caller-supplied. The catalog only authoritatively knows the served name, the
    backing HF model id, and the served context window — so those are the only
    fields this carries (see the §3 strategic decision in the migration plan)."""

    endpoint: str
    served_model_name: str
    hf_model_id: str
    max_model_len: int | None = None


def resolve_serving_facts(
    endpoint: str,
    *,
    config_dir: Path | None = None,
) -> ServingFacts:
    """Resolve one catalog endpoint into its transport facts.

    Replaces the deleted ``infer_stack.contracts.load_profile_contract``: reads
    the new ``catalog.yaml`` via ``infer_stack.leasing.Catalog`` and returns only
    the facts the catalog owns. Hardware-free (no GPU simulation) and pure-static
    (no live/rendered stack needed) — under default-B the gateway base_url is
    deterministic and supplied by the caller, so the resolver never has to probe
    a running deployment for a port."""
    leasing = _import_infer_stack_leasing()
    catalog_path = _infer_stack_config_root(config_dir) / "catalog.yaml"
    catalog = leasing.Catalog.load(catalog_path)
    request = catalog.resolve_endpoint(endpoint)
    if request.engine != "vllm":
        raise ValueError(
            f"catalog endpoint {endpoint!r} uses engine {request.engine!r}; "
            "benchmark export only supports vLLM endpoints."
        )
    served = request.served
    return ServingFacts(
        endpoint=endpoint,
        served_model_name=served["served_model_name"],
        hf_model_id=served["hf_model_id"],
        max_model_len=request.capacity.get("max_model_len"),
    )


def _benchmark_client_class(protocol_mode: str, access_kind: str) -> str:
    if access_kind == "vllm-direct":
        return "helm.clients.vllm_client.VLLMClient" if protocol_mode == "completions" else "helm.clients.vllm_client.VLLMChatClient"
    return (
        "helm.clients.openai_client.OpenAILegacyCompletionsClient"
        if protocol_mode == "completions"
        else "helm.clients.openai_client.OpenAIClient"
    )


def _default_gateway_base_url() -> str:
    return f"http://127.0.0.1:{DEFAULT_GATEWAY_PORT}/v1"


def _default_deployment_name(served_name: str, access_kind: str) -> str:
    prefix = "vllm" if access_kind == "vllm-direct" else "litellm"
    return f"{prefix}/{served_name}-local"


def _resolve_api_key(access_kind: str, *, api_key_value: str | None = None) -> str | None:
    # vllm-direct hits the vLLM server directly (no gateway auth); an explicit
    # value is forwarded verbatim, otherwise none is required.
    if access_kind == "vllm-direct":
        return api_key_value
    if api_key_value is not None:
        return api_key_value
    env_value = os.environ.get(LITELLM_AUTH_ENV)
    if env_value:
        return env_value
    raise ValueError(
        f"Selected access mode {access_kind!r} requires credentials via "
        f"{LITELLM_AUTH_ENV!r}; bundle was not written because credentials were missing."
    )


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
                "base_url": base_url or _default_gateway_base_url(),
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


def _helm_config_paths() -> tuple[Path, Path]:
    helm_root = repo_root() / "submodules" / "helm" / "src" / "helm" / "config"
    return helm_root / "model_metadata.yaml", helm_root / "tokenizer_configs.yaml"


def _assert_helm_aliases_exist(model_name: str, tokenizer_name: str) -> None:
    import yaml

    model_metadata_path, tokenizer_configs_path = _helm_config_paths()
    model_docs = yaml.safe_load(model_metadata_path.read_text(encoding="utf-8")) or {}
    tokenizer_docs = yaml.safe_load(tokenizer_configs_path.read_text(encoding="utf-8")) or {}
    known_models = {item.get("name") for item in model_docs.get("models", []) or []}
    known_tokenizers = {item.get("name") for item in tokenizer_docs.get("tokenizer_configs", []) or []}
    if model_name not in known_models:
        raise ValueError(
            f"HELM model alias missing for {model_name!r}; update the benchmark export override before launching the run."
        )
    if tokenizer_name not in known_tokenizers:
        raise ValueError(
            f"HELM tokenizer alias missing for {tokenizer_name!r}; update the benchmark export override before launching the run."
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


def _strip_local_deployment(
    run_entry: str, local_names: "frozenset[str]"
) -> tuple[str, str | None]:
    """Drop a ``model_deployment=<name>`` token from a run-entry for discovery,
    but ONLY when ``<name>`` is a LOCAL deployment (rel-path plan §6, the
    "local-only" rule). The official run dir never carries a local token, so it
    must be stripped to match; an official/private token (e.g. a stanfordhealthcare
    deployment) is kept so it still discriminates. Returns
    ``(discovery_query, stripped_local_name_or_None)``.
    """
    bench, sep, rest = run_entry.partition(":")
    if not sep:
        return run_entry, None
    kept: list[str] = []
    token: str | None = None
    for kv in rest.split(","):
        key, _, value = kv.partition("=")
        if key.strip() == "model_deployment" and value.strip() in local_names:
            token = value.strip()
        else:
            kept.append(kv)
    query = f"{bench}:{','.join(kept)}" if kept else bench
    return query, token


def _freeze_run_spec_sources(
    spec: dict[str, Any],
    *,
    precomputed_root: str,
    model_entries: list[dict[str, Any]],
    lease_facts: dict[str, Any] | None,
    runs: list[Any],
) -> list[dict[str, Any]]:
    """Resolve each preset run-entry to its EXACT rel-path once and freeze a
    ``run_spec_sources`` list (rel-path plan §4.5).

    This is the *only* remaining use of token-subset discovery: it runs here, at
    export, against a known corpus snapshot (``runs`` already enumerated under
    ``precomputed_root``), and pins the matched official run dir as a path relative
    to the root. The materialized-replay path then reads that exact path — no
    run-time discovery. A ``NO_MATCH`` / ``AMBIGUOUS`` entry is a hard error:
    freezing a wrong or ambiguously-chosen match would pin the wrong recipe.

    Each frozen source carries its own ``model_deployment`` (the LOCAL rewrite
    target) and ``lease_endpoint``, so a MULTI-deployment bundle freezes a per-run
    rewrite target — lifting the single-deployment restriction the discovery path
    imposes (``export_benchmark_bundle`` ``rewrite_deployment``).
    """
    from eval_audit.cli import check_precomputed_discovery as dc

    root = Path(precomputed_root)
    local_names = frozenset(entry["name"] for entry in model_entries)
    single_name = model_entries[0]["name"] if len(model_entries) == 1 else None
    lease_scalar = (lease_facts or {}).get("lease_endpoint")
    lease_map = (lease_facts or {}).get("lease_endpoints") or {}

    sources: list[dict[str, Any]] = []
    for run_entry in spec["run_entries"]:
        query, local_token = _strip_local_deployment(run_entry, local_names)
        deployment = local_token or single_name
        if deployment is None:
            raise ValueError(
                f"cannot freeze run-entry {run_entry!r}: a multi-deployment bundle "
                "needs an inline model_deployment=<local> token to name the rewrite "
                "target, but none was present."
            )
        result = dc._classify(query, runs)
        if result.status != "RESOLVED":
            raise ValueError(
                f"cannot freeze run-entry {run_entry!r}: discovery is "
                f"{result.status} under {precomputed_root!r} "
                f"({len(result.candidates)} candidates). Narrow precomputed_root or "
                "fix the entry before exporting an exact-path bundle."
            )
        rel_path = str(Path(result.best.path).relative_to(root))
        source: dict[str, Any] = {
            "run_entry": run_entry,
            "rel_path": rel_path,
            "model_deployment": deployment,
        }
        endpoint = lease_scalar or lease_map.get(deployment)
        if endpoint:
            source["lease_endpoint"] = endpoint
        sources.append(source)
    return sources


def _manifest_doc(
    *,
    spec: dict[str, Any],
    model_deployments_fpath: str,
    lease_facts: dict[str, Any] | None = None,
    from_run_spec: bool = False,
    precomputed_root: str | None = None,
    model_deployment: str | None = None,
    run_spec_sources: list[dict[str, Any]] | None = None,
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
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    preset_cfg = PRESET_CONFIGS.get(preset or "", {})
    specs = profile_specs or _profile_specs("", preset_cfg)
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
        # The generated model_deployments.yaml binds the bundle's NATIVE local
        # deployment name on BOTH paths (run-entry and from-spec). Under from-spec
        # the manifest's `model_deployment` field (set below) names this same entry
        # and the replay rewrites the run_spec.json's adapter_spec.model_deployment
        # to it — so the produced run records the local endpoint (same_deployment=no)
        # with the rewrite target and the registration agreeing by construction.
        # This supersedes the earlier by-name rekey to the official name; see
        # docs/planning/from-spec-deployment-rewrite-plan.md Change 5.
        deployment_name = spec.get("model_deployment_name")
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
        _assert_helm_aliases_exist(model_entries[-1]["model_name"], model_entries[-1]["tokenizer_name"])
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
    rewrite_deployment = (
        model_entries[0]["name"]
        if from_run_spec and len(model_entries) == 1
        else None
    )

    # Exact-path replay (rel-path plan §4.5): resolve each run-entry to its pinned
    # rel-path NOW, against the corpus snapshot, and freeze run_spec_sources into the
    # generated manifests. Discovery (token-subset) runs exactly here, once. The
    # corpus is enumerated once per distinct root and shared across smoke/full.
    smoke_sources = full_sources = None
    if freeze_rel_paths:
        from eval_audit.cli import check_precomputed_discovery as dc

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
        )
        full_sources = _freeze_run_spec_sources(
            full_spec, precomputed_root=full_root, model_entries=model_entries,
            lease_facts=lease_facts, runs=_runs_for(full_root),
        )

    benchmark_smoke_manifest = _manifest_doc(
        spec=smoke_spec,
        model_deployments_fpath=model_deployments_fpath,
        lease_facts=lease_facts,
        from_run_spec=from_run_spec,
        precomputed_root=precomputed_root,
        model_deployment=rewrite_deployment,
        run_spec_sources=smoke_sources,
    )
    benchmark_full_manifest = _manifest_doc(
        spec=full_spec,
        model_deployments_fpath=model_deployments_fpath,
        lease_facts=lease_facts,
        from_run_spec=from_run_spec,
        precomputed_root=precomputed_root,
        model_deployment=rewrite_deployment,
        run_spec_sources=full_sources,
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
) -> dict[str, Any]:
    # Exact-path replay is a from-spec variant: freezing rel-paths implies it.
    if freeze_rel_paths:
        from_run_spec = True
    preset_cfg = PRESET_CONFIGS.get(preset or "", {})
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
    )
