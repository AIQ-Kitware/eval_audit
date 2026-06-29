# e2e-tests operational notes

Gotchas that aren't enforced in code or config — they bite at run time, not at
review time. Keep this in sync with the `.venv` you run the grid in.

## Pin the e2e venv: `transformers >=4.53,<5` + `huggingface_hub == 0.36.2`

The `.venv` here must install **`transformers>=4.53,<5`** with
**`huggingface_hub==0.36.2`** (HELM's transitive version). Do **not** repair a
broken env by upgrading hub.

Why:

- `crfm-helm` only pins `transformers>=4.53,<6.0`, so a fresh resolve happily
  pulls **`transformers 5.x`**, which requires `huggingface-hub>=1.5.0` and does
  `from huggingface_hub import is_offline_mode` — a symbol **absent from hub
  0.36.2**. That `ImportError` fails the HELM→EEE rebuild inside Stage 5
  (`30_compose.sh` → `build_virtual_experiment` → `analyze_experiment`), so
  `analysis/experiment_summary.json` shows `n_built_reports: 0` /
  `skipped_run_entries[].reason = rebuild_failed`, and the Stage 6 summary
  (`40_build_summary.sh`) reports **"completed not analyzed"**.
- `transformers 4.x` ships its own `is_offline_mode` and is happy with hub
  0.36.2. Keep hub at 0.36.2 to match HELM; pin **transformers** down instead.

Repair (run from this folder, in your own shell where `.venv` resolves):

```bash
uv pip install --python .venv/bin/python 'transformers>=4.53,<5' 'huggingface_hub==0.36.2'
# verify: prints transformers 4.x | hub 0.36.2, and the import no longer raises
.venv/bin/python -c "import transformers,huggingface_hub as h; from huggingface_hub import is_offline_mode; print(transformers.__version__, h.__version__)"
```

Watch for a forced `tokenizers` change needing a py3.14 source (Rust) build — if
so, add the `tokenizers` range the chosen transformers wants to the install line.
Nothing in `pyproject.toml` encodes this — it's transitive, so the pin must be
applied at install time.

## from-spec hf conversion needs the run's `prod_env` (fixed in code)

The from-spec deployment-rewrite records `adapter_spec.model_deployment =
huggingface/phi-2-local` (to honestly report `same_deployment=no`). The HELM→EEE
converter must register the run's `prod_env/model_deployments.yaml`, or
`get_model_deployment("huggingface/phi-2-local")` misses the explicit entry and
HELM's dynamic `huggingface/<id>` generator eagerly
`AutoTokenizer.from_pretrained("phi-2-local")` → `OSError: not a local folder`,
re-triggering "completed not analyzed" for the hf scenario only (vLLM escapes —
no `vllm/<id>` generator). Fixed in
[`eval_audit/normalized/eee_artifacts.py`](../../eval_audit/normalized/eee_artifacts.py)
(`_register_run_local_helm_configs`, commit `b5c4cfe`) — noted here in case the
vendored converter (`every_eval_ever`) regresses the resolution.
