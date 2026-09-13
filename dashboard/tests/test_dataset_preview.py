"""Synthetic CSV fixtures only; no source patient records are copied into tests."""
import csv
import hashlib

import pytest
from fastapi.testclient import TestClient

from dashboard.api.dataset_preview import load_preview
from dashboard.api.main import create_app

STAY = {'SUBJECT_ID': '1', 'HADM_ID': '10', 'ICUSTAY_ID': '100', 'INTIME': '2100-01-02 01:00:00'}
ROW = {'ROW_ID': '1', 'SUBJECT_ID': '1', 'HADM_ID': '10', 'ICUSTAY_ID': '100',
       'ITEMID': '211', 'CHARTTIME': '2100-01-01 22:30:00',
       'VALUENUM': '70', 'VALUEUOM': 'BPM', 'ERROR': ''}


def write_csv(root, name, rows, fields=None):
    root.mkdir(parents=True, exist_ok=True)
    with (root / name).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def dataset(root, rows=None, stays=None):
    write_csv(root, 'selected_icu_stays.csv', stays or [STAY])
    write_csv(root, 'CHARTEVENTS.csv', rows if rows is not None else [ROW], list(ROW))
    return root


def test_composite_identity_keeps_encounters_separate(tmp_path):
    second = {**STAY, 'HADM_ID': '20', 'ICUSTAY_ID': '200'}
    rows = [ROW, {**ROW, 'ROW_ID': '2', 'HADM_ID': '20', 'ICUSTAY_ID': '200'},
            {**ROW, 'SUBJECT_ID': '2'}, {**ROW, 'HADM_ID': '20'},
            {**ROW, 'ICUSTAY_ID': '200'}]
    result = load_preview(dataset(tmp_path, rows, [STAY, second]))
    assert len(result['patients']) == 2
    assert result['stats']['unmatched'] == 3
    assert result['stats']['selected_points'] == 2
    assert result['stats']['subjects_with_data'] == 1
    assert all(len(p['metrics']['heart_rate']['points']) == 1 for p in result['patients'])


def test_metrics_original_clock_sorting_and_no_value_transformation(tmp_path):
    rows = [ROW, {**ROW, 'ITEMID': '220045', 'CHARTTIME': '2100-01-02 00:30:00', 'VALUENUM': '0'},
            {**ROW, 'ITEMID': '646', 'VALUEUOM': '%', 'VALUENUM': '98'},
            {**ROW, 'ITEMID': '220277', 'VALUEUOM': '%', 'VALUENUM': '99'}]
    result = load_preview(dataset(tmp_path, list(reversed(rows))))
    heart = result['patients'][0]['metrics']['heart_rate']
    assert [p['hours_from_icu'] for p in heart['points']] == [-2.5, -0.5]
    assert heart['points'][0]['measured_at'] == '2100-01-01 22:30:00'
    assert heart['points'][1]['value'] == 0
    assert heart['unit'] == 'bpm'
    assert len(result['patients'][0]['metrics']['spo2']['points']) == 2
    assert result['time_basis'] == 'source_clock_no_timezone'
    assert result['stats']['before_icu'] == 4


def test_bad_selected_rows_are_counted_without_fabrication(tmp_path):
    bad = [{'VALUENUM': ''}, {'VALUENUM': 'NaN'}, {'VALUENUM': 'inf'},
           {'CHARTTIME': 'bad-private-value'}, {'ERROR': '1'}, {'VALUEUOM': '%'},
           {'ITEMID': '646', 'VALUEUOM': 'fraction'}]
    rows = [ROW] + [{**ROW, **change} for change in bad] + [{**ROW, 'ITEMID': '999'}]
    result = load_preview(dataset(tmp_path, rows))
    assert result['stats']['invalid'] == 7
    assert result['stats']['unsupported_metric'] == 1
    assert result['stats']['selected_points'] == 1
    assert 'bad-private-value' not in str(result)


def test_missing_empty_and_duplicate_sources(tmp_path):
    assert load_preview(tmp_path)['status'] == 'missing'
    dataset(tmp_path, [])
    assert load_preview(tmp_path)['stats']['selected_points'] == 0
    dataset(tmp_path, stays=[STAY, STAY])
    with pytest.raises(ValueError, match='重复'):
        load_preview(tmp_path)


def test_api_preview_is_read_only_and_rereads_file_changes(tmp_path):
    source = dataset(tmp_path / 'source')
    original = {p.name: hashlib.sha256(p.read_bytes()).digest() for p in source.iterdir()}
    with TestClient(create_app(tmp_path / 'live', watch=False, dataset_root=source)) as client:
        revision = client.get('/api/health').json()['revision']
        before = {str(p.relative_to(tmp_path / 'live')) for p in (tmp_path / 'live').rglob('*')}
        response = client.get('/api/datasets/icu-preview')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        result = response.json()
        assert result == client.get('/api/datasets/icu-preview').json()
        assert client.get('/api/patients').json() == []
        assert client.get('/api/health').json()['revision'] == revision
        assert before == {str(p.relative_to(tmp_path / 'live')) for p in (tmp_path / 'live').rglob('*')}
        assert {p.name: hashlib.sha256(p.read_bytes()).digest() for p in source.iterdir()} == original
        assert 'risk' not in result['patients'][0] and 'name' not in result['patients'][0]
        dataset(source, [ROW, {**ROW, 'CHARTTIME': '2100-01-02 01:00:00'}])
        updated = client.get('/api/datasets/icu-preview').json()
        assert updated['stats']['selected_points'] == 2
        assert updated['stats']['at_or_after_icu'] == 1


def test_invalid_source_schema_and_dates_do_not_expose_values(tmp_path):
    source = dataset(tmp_path / 'source', stays=[{**STAY, 'INTIME': 'private-invalid-time'}])
    with TestClient(create_app(tmp_path / 'live', watch=False, dataset_root=source)) as client:
        response = client.get('/api/datasets/icu-preview')
        assert response.status_code == 422
        assert 'private-invalid-time' not in response.text
        write_csv(source, 'selected_icu_stays.csv', [{'wrong': 'private-value'}])
        response = client.get('/api/datasets/icu-preview')
        assert response.status_code == 422
        assert 'private-value' not in response.text
