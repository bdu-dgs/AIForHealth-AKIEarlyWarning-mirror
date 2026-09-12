"""Read-only MIMIC-III preview; never changes dates or the live patient store."""
import csv
import math
from collections import Counter
from datetime import datetime
from pathlib import Path


# MIT-LCP/mimic-code: mimic-iii/concepts_postgres/firstday/vitals_first_day.sql
METRICS = {
    'heart_rate': {'label': '心率', 'unit': 'bpm', 'items': {'211', '220045'}},
    'spo2': {'label': '血氧饱和度', 'unit': '%', 'items': {'646', '220277'}},
}
ITEMS = {item: metric for metric, config in METRICS.items() for item in config['items']}
KEY = ('SUBJECT_ID', 'HADM_ID', 'ICUSTAY_ID')


def source_time(value):
    # No timezone in source: never interpret these dates in the computer timezone.
    return datetime.strptime(value.strip(), '%Y-%m-%d %H:%M:%S')


def read_rows(path, required):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f'{path.name} 缺少必需的列，请检查文件格式')
        for number, row in enumerate(reader, start=1):
            if number > 200_000:
                raise ValueError('预览最多读取每个文件 200,000 行，请使用较小的数据子集')
            yield {key: (value or '').strip() for key, value in row.items() if key is not None}


def load_preview(root):
    root = Path(root)
    paths = [root / name for name in ('selected_icu_stays.csv', 'CHARTEVENTS.csv')]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        return {'status': 'missing', 'missing_files': missing, 'patients': [], 'stats': {}}
    before = [(path.stat().st_size, path.stat().st_mtime_ns) for path in paths]
    if any(size > 64 * 1024 * 1024 for size, _ in before):
        raise ValueError('只读预览支持每个 CSV 最大 64 MB，请使用较小的数据子集')
    patients = {}
    times = {}
    for row in read_rows(paths[0], set(KEY) | {'INTIME'}):
        key = tuple(row.get(field, '') for field in KEY)
        if not all(value.isascii() and value.isdigit() for value in key):
            raise ValueError('入选记录的患者、住院或 ICU ID 无效')
        if key in patients:
            raise ValueError('入选 ICU 记录存在重复，请先核对后重试')
        try:
            times[key] = source_time(row['INTIME'])
        except ValueError:
            raise ValueError('入选 ICU 记录的 INTIME 格式无效') from None
        patients[key] = {
            'subject_id': key[0], 'hadm_id': key[1], 'icustay_id': key[2],
            'icu_admitted_at': row['INTIME'],
            'metrics': {name: {'label': config['label'], 'unit': config['unit'], 'points': []}
                        for name, config in METRICS.items()},
        }
        if len(patients) > 2000:
            raise ValueError('预览最多支持 2,000 条入选 ICU 记录')
    stats = Counter(chart_rows=0, unsupported_metric=0, unmatched=0, invalid=0,
                    selected_points=0, before_icu=0, at_or_after_icu=0)
    for row in read_rows(paths[1], set(KEY) | {'ITEMID', 'CHARTTIME', 'VALUENUM', 'VALUEUOM', 'ERROR'}):
        stats['chart_rows'] += 1
        metric = ITEMS.get(row['ITEMID'])
        if metric is None:
            stats['unsupported_metric'] += 1
            continue
        key = tuple(row.get(field, '') for field in KEY)
        if key not in patients:
            stats['unmatched'] += 1
            continue
        try:
            measured = source_time(row['CHARTTIME'])
            value = float(row['VALUENUM'])
            valid = (math.isfinite(value) and row['ERROR'] in ('', '0', '0.0')
                     and row['VALUEUOM'].lower() == METRICS[metric]['unit'])
        except (ValueError, OverflowError):
            valid = False
        if not valid:
            stats['invalid'] += 1
            continue
        hours = (measured - times[key]).total_seconds() / 3600
        patients[key]['metrics'][metric]['points'].append({
            'record_id': row.get('ROW_ID', ''), 'item_id': row['ITEMID'],
            'measured_at': row['CHARTTIME'], 'hours_from_icu': hours, 'value': value,
        })
        stats['selected_points'] += 1
        stats['before_icu' if hours < 0 else 'at_or_after_icu'] += 1
    for patient in patients.values():
        for metric in patient['metrics'].values():
            metric['points'].sort(key=lambda point: point['hours_from_icu'])
    after = [(path.stat().st_size, path.stat().st_mtime_ns) for path in paths]
    if before != after:
        raise ValueError('数据文件正在更新，请稍后刷新')
    stats['selected_stays'] = len(patients)
    stats['stays_with_data'] = sum(any(metric['points'] for metric in patient['metrics'].values())
                                   for patient in patients.values())
    stats['subjects_with_data'] = len({p['subject_id'] for p in patients.values()
                                      if any(m['points'] for m in p['metrics'].values())})
    return {'status': 'ready', 'time_basis': 'source_clock_no_timezone', 'stats': dict(stats),
            'patients': sorted(patients.values(), key=lambda p: (int(p['subject_id']), int(p['icustay_id'])))}
