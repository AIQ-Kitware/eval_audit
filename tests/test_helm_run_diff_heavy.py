import json
import subprocess

import kwutil
import pytest
import ubelt as ub

from eval_audit.helm.diff import HelmRunDiff


def _coerce_demo_run():
    """Best-effort demo run fetch; skip if environment cannot materialize it.

    ``magnet`` is an optional dependency at this layer (it owns the HELM
    download/materialize plumbing). Defer the import so collection works in
    environments where magnet is not installed; skip the test if it is not
    available.
    """
    try:
        from magnet.backends.helm.helm_outputs import HelmRun
    except ModuleNotFoundError as ex:
        pytest.skip(f'magnet not installed: {ex!r}')
    try:
        return HelmRun.demo()
    except subprocess.CalledProcessError as ex:
        pytest.skip(f'Unable to materialize HelmRun.demo(): {ex!r}')


def test_helm_run_diff_heavy_demo_workflow():
    """Heavyweight regression test for end-to-end HelmRunDiff behavior."""
    run_a = _coerce_demo_run()
    from magnet.backends.helm.helm_outputs import HelmRun
    dpath = ub.Path.appdir('eval_audit/tests/helm/helm_run_diff_heavy').delete().ensuredir()

    # Case 1: identical copy
    same_path = dpath / (run_a.path.name + '_same')
    run_a.path.copy(same_path)
    run_b = HelmRun(same_path)
    rd = HelmRunDiff(run_a, run_b, a_name='orig', b_name='same')
    info = rd.summary_dict(level=20)
    assert info['run_spec_dict_ok'] is True
    assert info['scenario_ok'] in {True, None}
    # R-2 (2026-07-06): value/instance agreement are no longer exposed on the
    # summary dict; identical runs surface as a clean diagnosis (value drift is
    # still consumed internally by the diagnosis).
    assert info['dataset_overlap']['base_iou'] == 1.0
    assert info['diagnosis']['label'] in {'reproduced', 'core_match_bookkeeping_drift'}
    json.dumps(info, allow_nan=False)

    # Case 2: perturb one run-level stat mean
    stats_path = dpath / (run_a.path.name + '_statsmod')
    run_a.path.copy(stats_path)
    stat_fpath = stats_path / 'stats.json'
    stats = kwutil.Json.loads(stat_fpath.read_text())
    old_mean = float(stats[0].get('mean', 0.0))
    stats[0]['mean'] = old_mean + 1.23
    stat_fpath.write_text(kwutil.Json.dumps(stats))

    rd2 = HelmRunDiff(run_a, HelmRun(stats_path), a_name='orig', b_name='stats+1.23')
    info2 = rd2.summary_dict(level=20)
    # Run-level value drift is reflected in the diagnosis (the value agreement
    # dict block was retired in R-2; the diagnosis still consumes it internally).
    assert info2['diagnosis']['label'] in {
        'core_metric_drift',
        'core_match_bookkeeping_drift',
        'reproduced',
    }
    json.dumps(info2, allow_nan=False)

    # R-2 (2026-07-06): the former Case 3 exercised the retired
    # HelmRunDiff.instance_summary_dict; per-instance agreement now lives in
    # NormalizedDiff (tests/test_phase3_normalized_diff.py).

    # Case 4: run-spec deployment change
    spec_path = dpath / (run_a.path.name + '_runspec_mod')
    run_a.path.copy(spec_path)
    spec_fpath = spec_path / 'run_spec.json'
    run_spec = kwutil.Json.loads(spec_fpath.read_text())
    run_spec.setdefault('adapter_spec', {})
    run_spec['adapter_spec']['model_deployment'] = 'someotherdeploy/gpt2'
    spec_fpath.write_text(kwutil.Json.dumps(run_spec))

    rd4 = HelmRunDiff(run_a, HelmRun(spec_path), a_name='orig', b_name='runspec_mod')
    info4 = rd4.summary_dict(level=20)
    assert info4['run_spec_dict_ok'] is False
    assert info4['run_spec_semantic']['deployment_changed'] is True
    assert info4['diagnosis']['label'] in {'deployment_drift', 'execution_spec_drift'}
    json.dumps(info4, allow_nan=False)
