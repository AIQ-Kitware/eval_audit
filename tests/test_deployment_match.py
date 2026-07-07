"""Tests for the deployment-match dev tool (dev/tools/deployment_match/).

The tool lives under dev/ (not on the default import path / not collected), so we
add it to sys.path here. Covers: oracle extraction from a real HELM fixture,
registry (official facts + the pure tokenizer post-processor predicate + source
override), grid shape (two tiers, distinct endpoints, sibling-tokenizer axis),
scorer ranking, and — when infer_stack is importable — that the generated catalog
endpoints don't coalesce (distinct compat-keys).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "dev" / "tools" / "deployment_match"
if str(TOOL) not in sys.path:
    sys.path.insert(0, str(TOOL))

import grid as grid_mod          # noqa: E402
import oracle as oracle_mod      # noqa: E402
import registry as registry_mod  # noqa: E402
import score as score_mod        # noqa: E402

GPT2_FIXTURE = (REPO / "submodules" / "every_eval_ever" / "tests" / "data" / "helm"
                / "narrative_qa:model=openai_gpt2")


# --------------------------------------------------------------------------- #
# oracle
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not GPT2_FIXTURE.exists(), reason="gpt2 HELM fixture missing")
def test_oracle_extracts_recipe_and_official_completions():
    orc = oracle_mod.load_oracle(GPT2_FIXTURE, n=3)
    assert orc.model == "openai/gpt2"
    assert orc.model_deployment  # e.g. huggingface/gpt2
    assert len(orc.sample) == 3
    assert all(s.prompt for s in orc.sample)
    assert oracle_mod.has_official_completions(orc)
    # recipe params are captured and are what we replay fixed
    assert "max_tokens" in orc.recipe


@pytest.mark.skipif(not GPT2_FIXTURE.exists(), reason="gpt2 HELM fixture missing")
def test_oracle_sample_never_exceeds_available():
    orc = oracle_mod.load_oracle(GPT2_FIXTURE, n=10_000)
    assert len(orc.sample) == orc.n_available


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
def test_post_processor_predicate():
    p = registry_mod.post_processor_appends_special
    template_with_special = {
        "type": "TemplateProcessing",
        "single": [{"Sequence": {"id": "A"}}, {"SpecialToken": {"id": "<|endoftext|>"}}],
    }
    template_plain = {"type": "TemplateProcessing", "single": [{"Sequence": {"id": "A"}}]}
    seq = {"type": "Sequence", "processors": [template_plain, template_with_special]}
    assert p(template_with_special) is True
    assert p(template_plain) is False
    assert p(seq) is True          # recurses into Sequence
    assert p(None) is False
    assert p({}) is False


def test_resolve_official_deployment_olmo():
    facts = registry_mod.resolve_official_deployment("together/olmo-7b")
    # From submodules/helm/.../model_deployments.yaml.
    assert facts.get("max_sequence_length") == 2047
    assert facts.get("tokenizer_name") == "allenai/olmo-7b"


def test_resolve_source_override_wins():
    res = registry_mod.resolve("some/model", "", source_override="hf-org/Custom")
    assert res.hf_source == "hf-org/Custom"
    # protocol defaults + flagged unresolved when nothing provides it
    assert res.protocol == registry_mod.DEFAULT_PROTOCOL
    assert res.protocol_resolved is False


# --------------------------------------------------------------------------- #
# grid
# --------------------------------------------------------------------------- #
def _resolution(**kw):
    base = dict(model="allenai/olmo-7b", hf_source="allenai/OLMo-7B-hf",
                protocol="completions", protocol_resolved=True,
                official_max_sequence_length=2047,
                tokenizer_appends_special=True,
                tokenizer_sibling="allenai/OLMo-1.7-7B-hf")
    base.update(kw)
    return registry_mod.Resolution(**base)


def test_grid_two_tiers_and_distinct_endpoints():
    g = grid_mod.build_grid(_resolution())
    # default: 4 dtype x {default + sibling} = 8 serve; 2 add_special_tokens x 1 protocol = 2 request
    assert len(g.serve_recipes) == 8
    assert len(g.request_variants) == 2
    assert len(g.cells) == 16
    names = [s.name for s in g.serve_recipes]
    assert len(set(names)) == len(names)             # distinct endpoint names
    # every serve recipe passes an explicit --dtype
    for sr in g.serve_recipes:
        assert "--dtype" in sr.extra_args()
    # the sibling tokenizer became a serve-time candidate
    assert any(sr.tokenizer == "allenai/OLMo-1.7-7B-hf" for sr in g.serve_recipes)
    # request tier sweeps add_special_tokens true+false
    assert {rv.add_special_tokens for rv in g.request_variants} == {True, False}


def test_grid_no_sibling_when_tokenizer_clean():
    g = grid_mod.build_grid(_resolution(tokenizer_appends_special=False,
                                        tokenizer_sibling=None))
    assert len(g.serve_recipes) == 4                 # dtype only, no tokenizer axis
    assert all(sr.tokenizer is None for sr in g.serve_recipes)


def test_grid_cap_drops_and_reports():
    g = grid_mod.build_grid(_resolution(), spec={"cap": 5})
    assert len(g.cells) == 5
    assert g.capped == 11
    assert any("cap" in n for n in g.notes)


def test_grid_max_model_len_clamped_to_model_ceiling():
    """official+1 must never exceed max_position_embeddings — vLLM refuses to
    start above the derived ceiling (the olmoe 4096+1=4097 failure)."""
    # HELM window-1 convention (together/olmo-7b: 2047, model ceiling 2048): +1 fits.
    g = grid_mod.build_grid(_resolution(official_max_sequence_length=2047,
                                        hf_max_position_embeddings=2048))
    assert all(sr.max_model_len == 2048 for sr in g.serve_recipes)
    # HELM full-window convention (huggingface/olmoe: 4096 == ceiling): clamp, no +1.
    g = grid_mod.build_grid(_resolution(official_max_sequence_length=4096,
                                        hf_max_position_embeddings=4096))
    assert all(sr.max_model_len == 4096 for sr in g.serve_recipes)


def test_grid_max_model_len_conservative_without_config():
    """No cached config.json -> unknown ceiling -> official verbatim (never overshoot)."""
    g = grid_mod.build_grid(_resolution(official_max_sequence_length=4096,
                                        hf_max_position_embeddings=None))
    assert all(sr.max_model_len == 4096 for sr in g.serve_recipes)
    assert any("config.json" in n for n in g.notes)


# --------------------------------------------------------------------------- #
# grid: hf-match profile (vLLM<->HF determinism knobs)
# --------------------------------------------------------------------------- #
def test_default_grid_keeps_vllm_defaults():
    """The default grid must NOT emit the determinism-off flags (vLLM defaults)."""
    g = grid_mod.build_grid(_resolution())
    for sr in g.serve_recipes:
        ea = sr.extra_args()
        assert "--no-enable-chunked-prefill" not in ea
        assert "--no-enable-prefix-caching" not in ea


def test_hf_match_profile_pins_determinism_knobs():
    spec = grid_mod.BUILTIN_PROFILES["hf-match"]
    g = grid_mod.build_grid(_resolution(), spec=spec)
    for sr in g.serve_recipes:
        ea = sr.extra_args()
        assert "--enforce-eager" in ea
        assert "--no-enable-chunked-prefill" in ea
        assert "--no-enable-prefix-caching" in ea
        assert sr.runtime["max_num_seqs"] == 1
        # HF-eager's default attention backend, pinned (single value, not swept)
        assert sr.attention_backend == "TORCH_SDPA"
    # the determinism activation is surfaced as a grid note
    assert any("hf-match" in n for n in g.notes)
    # and it reaches the infer-stack catalog endpoint (serialized batching + backend)
    ep = g.serve_recipes[0].endpoint_dict("completions")
    assert ep["runtime"]["max_num_seqs"] == 1
    assert ep["runtime"]["attention_backend"] == "TORCH_SDPA"


def test_default_grid_omits_attention_backend():
    """The default grid leaves vLLM's own attention backend (no env var set)."""
    g = grid_mod.build_grid(_resolution())
    for sr in g.serve_recipes:
        assert sr.attention_backend is None
        assert "attention_backend" not in sr.endpoint_dict("completions")["runtime"]


def test_attention_backend_axis_widens_and_names_endpoints():
    """Sweeping the backend produces distinct, backend-named serve endpoints."""
    g = grid_mod.build_grid(
        _resolution(tokenizer_appends_special=False, tokenizer_sibling=None),
        spec={"axes": {"attention_backend": ["TORCH_SDPA", "FLASH_ATTN"]}})
    assert len(g.serve_recipes) == 8            # 4 dtype x 2 backends
    names = [s.name for s in g.serve_recipes]
    assert len(set(names)) == 8                 # distinct endpoint names
    assert any("attntorch-sdpa" in n for n in names)
    assert any("attnflash-attn" in n for n in names)
    assert {s.attention_backend for s in g.serve_recipes} == {"TORCH_SDPA", "FLASH_ATTN"}


# --------------------------------------------------------------------------- #
# score
# --------------------------------------------------------------------------- #
def test_score_ranks_match_over_collapse():
    oracle = [
        {"instance_id": "a", "official_completion": " Diana"},
        {"instance_id": "b", "official_completion": " The system"},
        {"instance_id": "c", "official_completion": " Paris"},
    ]

    def cell(cid, fn):
        return {"cell_id": cid, "endpoint": cid, "request": {},
                "results": [{"instance_id": o["instance_id"], "completion": fn(o),
                             "first_token": None, "error": None} for o in oracle]}

    perfect = cell("perfect", lambda o: o["official_completion"])
    boiler = "the first thing you need to do is make sure you have a good setup here"
    collapsed = cell("collapsed", lambda o: " " + boiler)

    ranked = score_mod.rank([collapsed, perfect], oracle)
    assert ranked[0]["cell_id"] == "perfect"
    assert ranked[0]["verdict"] == "MATCH"
    assert ranked[0]["quasi_match_rate"] == 1.0
    assert ranked[-1]["cell_id"] == "collapsed"
    assert ranked[-1]["verdict"] == "COLLAPSED"
    assert ranked[0]["composite"] > ranked[-1]["composite"]


def test_score_selftest_passes():
    assert score_mod.selftest() == 0


# --------------------------------------------------------------------------- #
# grid -> infer-stack catalog: endpoints must not coalesce
# --------------------------------------------------------------------------- #
def test_catalog_endpoints_have_distinct_compat_keys():
    infer_stack = REPO / "submodules" / "infer_stack"
    if str(infer_stack) not in sys.path:
        sys.path.insert(0, str(infer_stack))
    catalog_mod = pytest.importorskip("infer_stack.leasing.catalog")

    g = grid_mod.build_grid(_resolution())
    cat = catalog_mod.Catalog.from_dict(g.to_catalog())
    keys = {n: cat.resolve_endpoint(n).compat_key for n in cat.endpoints}
    assert len(cat.endpoints) == len(g.serve_recipes)
    assert len(set(keys.values())) == len(keys)      # no coalescing
