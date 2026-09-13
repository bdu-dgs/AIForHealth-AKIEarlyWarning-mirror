"""Synthetic, isolated tests only. No MIMIC or patient data is used."""
import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from dashboard.api.exchange import pack, unpack
from dashboard.api.main import create_app
from dashboard.api.schemas import InputBatch, PredictionBatch, stamp, now
from dashboard.api.storage import Store, atomic_json
from dashboard.api.watcher import Watcher


PATIENT = {'patient_id': 'synthetic_01', 'encounter_id': 'synthetic_stay', 'name': '合成测试患者',
           'icu_admitted_at': '2020-01-01T00:00:00Z'}


def observation(record='o1', value=1.2, measured='2020-01-01T01:00:00Z', available='2020-01-01T01:05:00Z', **kwargs):
    return {'record_id': record, 'patient_id': PATIENT['patient_id'], 'encounter_id': PATIENT['encounter_id'],
            'metric': 'synthetic_measure', 'label': '合成测量', 'unit': 'test-unit', 'value': value,
            'measured_at': measured, 'available_at': available, **kwargs}


@pytest.fixture
def store(tmp_path):
    item = Store(tmp_path / 'isolated-data')
    item.ingest(InputBatch(patients=[PATIENT]))
    return item


def history(store, as_of='2020-01-03T00:00:00Z', kind='observation', start='2020-01-01T00:00:00Z', end='2020-01-03T00:00:00Z'):
    return store.history(PATIENT['patient_id'], stamp(start), stamp(end), stamp(as_of), kind)['items']


def prediction(store, **updates):
    base = {'record_id': 'p1', 'patient_id': PATIENT['patient_id'], 'encounter_id': PATIENT['encounter_id'],
            'input_revision': store.patient(PATIENT['patient_id'])['input_revision'],
            'input_fingerprint': store.input_snapshot(PATIENT['patient_id'], stamp('2020-01-01T02:00:00Z'), stamp('2020-01-01T01:00:00Z')),
            'model_id': 'synthetic-test-only', 'model_version': 'fixture-v1', 'target': 'synthetic AKI outcome',
            'origin_time': '2020-01-01T02:00:00Z', 'data_cutoff': '2020-01-01T01:00:00Z',
            'horizon_end': '2020-01-01T02:37:00Z', 'generated_at': '2020-01-01T02:00:00Z',
            'available_at': '2020-01-01T02:01:00Z', 'risk': .123}
    return {**base, **updates}


def test_repeated_import_export_and_restore_are_idempotent(store, tmp_path):
    batch = InputBatch(observations=[observation(available=None)])
    assert store.ingest(batch)['inserted'] == 1
    assert store.ingest(batch)['inserted'] == 0
    exported = store.export(PATIENT['patient_id'], 'input')
    assert store.ingest(InputBatch(**exported))['inserted'] == 0
    restored = Store(tmp_path / 'restored')
    restored.ingest(InputBatch(**exported))
    assert restored.patient(PATIENT['patient_id'])['input_fingerprint'] == store.patient(PATIENT['patient_id'])['input_fingerprint']


def test_batch_rolls_back_on_content_conflict(store):
    store.ingest(InputBatch(observations=[observation()]))
    before = store.revision
    with pytest.raises(ValueError, match='revision'):
        store.ingest(InputBatch(observations=[observation('new'), observation(value=99)]))
    assert store.revision == before
    assert [r['record_id'] for r in history(store)] == ['o1']


def test_replay_excludes_late_entries_and_uses_old_revision(store):
    store.ingest(InputBatch(observations=[observation()]))
    store.ingest(InputBatch(observations=[observation(value=8, revision=2, available='2020-01-02T00:00:00Z')]))
    assert history(store, '2020-01-01T01:04:00Z') == []
    assert history(store, '2020-01-01T02:00:00Z')[0]['value'] == 1.2
    assert history(store)[0]['value'] == 8


def test_moved_timestamp_does_not_resurrect_superseded_record(store):
    store.ingest(InputBatch(observations=[observation()]))
    store.ingest(InputBatch(observations=[observation(revision=2, measured='2020-01-01T00:30:00Z', available='2020-01-02T00:00:00Z')]))
    assert history(store, start='2020-01-01T00:45:00Z') == []
    assert store.patients()[0]['latest_measurement'] == stamp('2020-01-01T00:30:00Z')


def test_local_received_time_is_not_backdated(store):
    store.ingest(InputBatch(observations=[observation(available=None)]))
    assert history(store) == []
    recent = history(store, now(), end=now())
    assert recent[0]['availability_basis'] == 'local_received'


def test_prediction_requires_exact_visible_input_snapshot(store):
    store.ingest(InputBatch(observations=[observation()]))
    store.ingest(PredictionBatch(predictions=[prediction(store)]))
    assert history(store, kind='prediction')[0]['risk'] == .123
    assert history(store, as_of='2020-01-01T02:00:30Z', kind='prediction') == []
    with pytest.raises(ValueError, match='快照'):
        store.ingest(PredictionBatch(predictions=[prediction(store, record_id='bad', input_fingerprint='0' * 64)]))


def test_future_information_cannot_enter_old_prediction(store):
    store.ingest(InputBatch(observations=[observation()]))
    old = prediction(store)
    store.ingest(InputBatch(observations=[observation('late', measured='2020-01-01T00:50:00Z', available='2020-01-02T00:00:00Z')]))
    wrong = {**old, 'input_fingerprint': store.patient(PATIENT['patient_id'])['input_fingerprint']}
    with pytest.raises(ValueError, match='快照'):
        store.ingest(PredictionBatch(predictions=[wrong]))
    store.ingest(PredictionBatch(predictions=[old]))
    assert store.patients()[0]['stale'] is True


def test_cross_machine_predictions_use_fingerprint_not_local_batch_count(store, tmp_path):
    store.ingest(InputBatch(observations=[observation()]))
    store.ingest(InputBatch(observations=[observation('o2')]))
    output = prediction(store)
    store.ingest(PredictionBatch(predictions=[output]))
    other = Store(tmp_path / 'other-machine')
    other.ingest(InputBatch(**store.export(PATIENT['patient_id'], 'input')))
    other.ingest(PredictionBatch(**store.export(PATIENT['patient_id'], 'prediction')))
    assert other.patients()[0]['stale'] is False
    assert other.patient(PATIENT['patient_id'])['input_revision'] != output['input_revision']


def test_no_model_fabrication_and_quality_raw_counts(store):
    store.ingest(InputBatch(observations=[observation()]))
    assert store.patients()[0]['prediction'] is None
    quality = store.quality(PATIENT['patient_id'], stamp('2020-01-01T01:30:00Z'))
    assert quality['score'] is None
    assert quality['metrics'][0]['last_hour_count'] == 1
    assert quality['metrics'][0]['age_seconds'] == 1800
    request = json.loads((store.root / 'requests/synthetic_01.json').read_text(encoding='utf-8'))
    assert request['status'] == 'awaiting_model'
    assert request['input_fingerprint'] == store.patient(PATIENT['patient_id'])['input_fingerprint']


def test_validation_and_patient_isolation(store):
    with pytest.raises(ValueError):
        store.ingest(InputBatch(observations=[observation(patient_id='other')]))
    for change in ({'value': float('nan')}, {'measured_at': '2020-01-01T01:00:00'}, {'available_at': '2019-01-01T00:00:00Z'}):
        with pytest.raises(ValueError):
            store.ingest(InputBatch(observations=[observation(**change)]))


def test_watcher_atomic_write_duplicate_and_same_stat_replacement(store):
    watcher = Watcher(store)
    path = store.root / 'inbox/input/batch.json'
    body = InputBatch(observations=[observation()]).model_dump(mode='json')
    atomic_json(path, body)
    old_time = time.time() - 10; os.utime(path, (old_time, old_time))
    watcher.scan()
    assert len(history(store)) == 1
    revision = store.revision; watcher.scan(); assert store.revision == revision
    body['observations'][0]['value'] = 9.2
    # Same byte length and mtime, but changed content must be checked.
    atomic_json(path, body); os.utime(path, (old_time, old_time)); watcher.scan()
    assert watcher.status()['errors']
    assert history(store)[0]['value'] == 1.2
    path.write_text('{partial', encoding='utf-8'); os.utime(path, (old_time, old_time)); watcher.scan()
    assert history(store)[0]['value'] == 1.2


def test_real_watchdog_event_imports_without_manual_scan(store):
    watcher = Watcher(store); watcher.start()
    try:
        atomic_json(store.root / 'inbox/input/live.json', InputBatch(observations=[observation()]).model_dump(mode='json'))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not history(store):
            time.sleep(.05)
        assert len(history(store)) == 1
    finally:
        watcher.close()


def test_pagination_and_large_exchange_package(store):
    observations = [observation('o' + str(i)) for i in range(10001)]
    for start in range(0, len(observations), 1000):
        store.ingest(InputBatch(observations=observations[start:start+1000]))
    first = store.history(PATIENT['patient_id'], stamp('2020-01-01T00:00:00Z'), stamp('2020-01-03T00:00:00Z'), stamp('2020-01-03T00:00:00Z'), 'observation', limit=3000)
    assert len(first['items']) == 3000 and first['next_offset'] == 3000
    batches = unpack(pack(store.export(PATIENT['patient_id'], 'input')), 'history.zip')
    assert sum(len(b.observations) for b in batches) == 10001
    assert all(len(b.observations) <= 1000 for b in batches)


def test_api_validation_origin_guard_photo_and_offline_shell(tmp_path):
    with TestClient(create_app(tmp_path / 'api', watch=False)) as client:
        assert client.get('/api/health').json()['model_status'] == 'not_configured'
        assert client.post('/api/import', json={'kind': 'input', 'patients': [PATIENT]}).status_code == 200
        invalid = client.post('/api/import', json={'kind': 'input', 'observations': [observation(value='PRIVATE_INVALID')]})
        assert invalid.status_code == 422 and 'PRIVATE_INVALID' not in invalid.text
        assert client.post('/api/import', json={'kind':'input'}, headers={'Origin':'https://external.example'}).status_code == 403
        assert client.get('/api/patients/missing').status_code == 404
        picture = io.BytesIO(); Image.new('RGB', (16,16), 'white').save(picture, 'PNG')
        assert client.post('/api/patients/synthetic_01/photo', files={'photo':('fixture.png',picture.getvalue(),'image/png')}).status_code == 200
        assert client.get('/api/patients/synthetic_01/photo').headers['content-type'] == 'image/jpeg'
        assert client.get('/patients/synthetic_01').status_code == 200


def test_api_zip_import_and_streamed_body_limit(tmp_path):
    with TestClient(create_app(tmp_path / 'api', watch=False)) as client:
        content = pack(InputBatch(patients=[PATIENT], observations=[observation()]).model_dump(mode='json'))
        result = client.post('/api/import-file', files={'file':('exchange.zip',content,'application/zip')})
        assert result.status_code == 200 and result.json()['inserted'] == 1
        assert client.post('/api/import-file', files={'file':('exchange.zip',content,'application/zip')}).json()['inserted'] == 0
        result = client.post('/api/import', content=(b' ' * (1024 * 1024) for _ in range(18)), headers={'Content-Type':'application/json'})
        assert result.status_code == 413


def test_100_patient_burst_persists_each_patient_once(tmp_path):
    store = Store(tmp_path / 'burst')
    patients = [{**PATIENT, 'patient_id': f'synthetic_{i:03}', 'encounter_id': f'stay_{i:03}'} for i in range(100)]
    store.ingest(InputBatch(patients=patients))
    def send(p):
        return store.ingest(InputBatch(observations=[observation(patient_id=p['patient_id'], encounter_id=p['encounter_id'])]))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send, patients))
    assert sum(x['inserted'] for x in results) == 100
    assert len(store.patients()) == 100
    assert all(p['latest_measurement'] for p in store.patients())


def test_current_result_is_independent_of_plot_range_and_tracks_replay_snapshot(store):
    store.ingest(InputBatch(observations=[observation()]))
    first = prediction(store, horizon_end='2020-01-02T02:00:00Z')
    store.ingest(PredictionBatch(predictions=[first]))
    later = stamp('2020-01-01T12:00:00Z')
    assert history(store, as_of=later, kind='prediction', start='2020-01-01T06:00:00Z') == []
    assert store.current_predictions(PATIENT['patient_id'], later)['predictions'][0]['record_id'] == 'p1'
    store.ingest(InputBatch(observations=[observation('new', measured='2020-01-01T04:00:00Z', available='2020-01-01T05:00:00Z')]))
    view = store.current_predictions(PATIENT['patient_id'], later)
    assert view['predictions'][0]['input_fingerprint'] != view['input_fingerprint']


def test_accepted_files_preserve_availability_without_actor_identity(store):
    store.ingest(InputBatch(observations=[observation(available=None)], actor='test-private-actor'))
    contents = [json.loads(p.read_text(encoding='utf-8')) for p in (store.root / 'accepted/input').glob('*.json')]
    batch = next(v for v in contents if v['observations'])
    assert 'actor' not in batch
    assert batch['observations'][0]['available_at'] is not None


def test_export_volumes_stay_importable_and_cover_all_rows(store):
    from dashboard.api.exchange import volumes
    value = InputBatch(patients=[PATIENT], observations=[]).model_dump(mode='json')
    value['observations'] = [observation('r'+str(i)) for i in range(25001)]
    parts = volumes(value)
    assert len(parts) == 3
    restored = [item for part in parts for batch in unpack(pack(part), 'volume.zip') for item in batch.observations]
    assert len(restored) == 25001
    assert len({item.record_id for item in restored}) == 25001
