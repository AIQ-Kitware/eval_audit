import json
from typing import Any

from eval_audit.helm.analysis import HelmRunAnalysis
from eval_audit.helm.diff import HelmRunDiff
from eval_audit.helm.diff import dataset_overlap_from_request_states


def _dummy_analysis(run_spec: dict[str, Any], stats: list[dict[str, Any]]):
    ana = HelmRunAnalysis.__new__(HelmRunAnalysis)
    ana._raw_cache = {}
    ana._cache = {}
    ana.run = None
    ana.name = None
    ana.run_spec = lambda: run_spec
    ana.scenario = lambda: {'class_name': 'ToyScenario', 'output_path': '/tmp/a'}
    ana.scenario_state = lambda: {'request_states': []}
    ana.stats = lambda: stats
    return ana


# R-2 (2026-07-06): test_instance_summary_serializable was removed with
# HelmRunDiff.instance_summary_dict / the level>=20 instance_value_agreement
# block. Per-instance agreement (and its JSON-serializability) is now served by
# NormalizedDiff — see tests/test_phase3_normalized_diff.py.


def test_dataset_overlap_json_serializable():
    """Pure request_state overlap helper should stay strictly JSON-compatible."""
    rs_a = [
        {
            'instance': {'id': 'id1', 'split': 'test', 'input': {'text': 'Q1'}},
            'train_trial_index': 0,
            'request': {'prompt': 'P1'},
            'result': {'completions': [{'text': 'A1'}]},
        },
        {
            'instance': {
                'id': 'id1',
                'split': 'test',
                'input': {'text': 'Q1'},
                'perturbation': {'name': 'dialect', 'prob': 1.0},
            },
            'train_trial_index': 0,
            'request': {'prompt': 'P1-d'},
            'result': {'completions': [{'text': 'A1d'}]},
        },
    ]
    rs_b = [
        {
            'instance': {'id': 'id1', 'split': 'test', 'input': {'text': 'Q1'}},
            'train_trial_index': 0,
            'request': {'prompt': 'P1x'},
            'result': {'completions': [{'text': 'A1'}]},
        },
    ]
    info = dataset_overlap_from_request_states(rs_a, rs_b, max_examples=3)
    assert info['base_coverage']['n_isect'] == 1
    assert info['variant_coverage']['only_a'] == 1
    assert info['content_equality']['prompt']['equal_ratio'] == 0.0
    json.dumps(info, allow_nan=False)


def test_run_spec_metric_order_semantic_and_deployment_reason_values():
    """Metric list order should not create semantic eval drift; deployment reason should include values."""
    stats = [
        {
            'name': {'name': 'exact_match', 'split': 'test'},
            'count': 1,
            'mean': 1.0,
        }
    ]
    spec_a = {
        'name': 'toy',
        'adapter_spec': {'model': 'm', 'model_deployment': 'dep/A'},
        'metric_specs': [
            {'class_name': 'M0', 'args': {'x': 0}},
            {'class_name': 'M1', 'args': {'x': 1}},
        ],
    }
    spec_b_order_only = {
        'name': 'toy',
        'adapter_spec': {'model': 'm', 'model_deployment': 'dep/A'},
        'metric_specs': [
            {'class_name': 'M1', 'args': {'x': 1}},
            {'class_name': 'M0', 'args': {'x': 0}},
        ],
    }
    rd_order = HelmRunDiff(
        _dummy_analysis(spec_a, stats),
        _dummy_analysis(spec_b_order_only, stats),
        a_name='A',
        b_name='B',
    )
    info_order = rd_order.summary_dict(level=20)
    assert info_order['run_spec_dict_ok'] is False
    assert info_order['run_spec_semantic_dict_ok'] is True
    assert info_order['run_spec_semantic']['metric_specs_multiset_delta']['equal_as_multiset'] is True
    reason_names_order = [r['name'] for r in info_order['diagnosis']['reasons']]
    assert 'evaluation_spec_drift' not in reason_names_order
    json.dumps(info_order, allow_nan=False)

    spec_b_deploy = {
        'name': 'toy',
        'adapter_spec': {'model': 'm', 'model_deployment': 'dep/B'},
        'metric_specs': spec_a['metric_specs'],
    }
    rd_dep = HelmRunDiff(
        _dummy_analysis(spec_a, stats),
        _dummy_analysis(spec_b_deploy, stats),
        a_name='A',
        b_name='B',
    )
    info_dep = rd_dep.summary_dict(level=20)
    reasons = {r['name']: r for r in info_dep['diagnosis']['reasons']}
    assert 'deployment_drift' in reasons
    assert reasons['deployment_drift']['details']['a_value'] == 'dep/A'
    assert reasons['deployment_drift']['details']['b_value'] == 'dep/B'
    exec_examples = info_dep['run_spec_semantic']['execution_value_examples']
    assert any(ex.get('path') == 'adapter_spec.model_deployment' for ex in exec_examples)
    json.dumps(info_dep, allow_nan=False)
