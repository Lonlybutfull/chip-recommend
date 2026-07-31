#!/usr/bin/env python3
"""Import benchmarks and compat data from parse11/parse1.db into data.db."""
import sqlite3, sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

P11 = "E:/BUPT_PS/P_0/芯片+模型/parse11/parse1/data/parse1.db"

def main():
    p11 = sqlite3.connect(P11)
    p11.row_factory = sqlite3.Row
    db = sqlite3.connect(str(HERE / "data" / "data.db"))
    db.row_factory = sqlite3.Row
    now = datetime.now().isoformat(timespec='seconds')

    # Load chip name mapping
    chip_names = {r['chip_model']: r['id'] for r in db.execute('SELECT id, chip_model FROM chips').fetchall()}
    model_ids = {r['model_id'] for r in db.execute('SELECT model_id FROM models').fetchall()}

    # ---- BENCHMARKS ----
    bench_rows = p11.execute('SELECT * FROM benchmarks').fetchall()
    print(f'Parse11 benchmarks: {len(bench_rows)}')

    ins = 0; skip = 0
    for row in bench_rows:
        d = dict(row)
        hw = (d.get('hardware_sku') or '').strip()
        mid = (d.get('model_id') or '').strip()
        if not hw or not mid:
            skip += 1; continue
        if mid not in model_ids:
            skip += 1; continue

        # Match chip
        matched = hw
        for cm in chip_names:
            if hw.lower().replace(' ','') in cm.lower().replace(' ','') or \
               cm.lower().replace(' ','') in hw.lower().replace(' ',''):
                matched = cm; break

        # Dedup
        wl = d.get('workload_type') or 'inference'
        dup = db.execute(
            'SELECT id FROM chip_model_benchmarks WHERE chip_model=? AND model_id=? AND workload_type=?',
            (matched, mid, wl)).fetchone()
        if dup:
            skip += 1; continue

        thr = ''
        if d.get('throughput') and d.get('throughput_unit'):
            thr = f"{d['throughput']} {d['throughput_unit']}"

        fields = {
            'chip_model': matched,
            'model_id': mid,
            'workload_type': wl,
            'suite_name': d.get('suite_name', d.get('benchmark_category', 'community')),
            'throughput_tok_s': thr,
            'mfu_pct': str(d.get('mfu_pct') or ''),
            'memory_peak_mb': str(d.get('memory_peak_mb') or ''),
            'precision': d.get('precision') or '',
            'test_date': d.get('test_date') or '',
            'notes': d.get('notes') or '',
        }
        fields = {k: v for k, v in fields.items() if v and v.strip()}
        if 'chip_model' not in fields or 'model_id' not in fields:
            skip += 1; continue

        cols = list(fields.keys()); vals = list(fields.values())
        db.execute(
            f"INSERT INTO chip_model_benchmarks ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})",
            vals)
        new_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

        db.execute(
            "INSERT INTO field_provenance (table_name, row_id, field_name, field_label, old_value, new_value, "
            "source_type, source_url, source_detail, confidence, is_official, updated_at, notes) "
            "VALUES (?, ?, ?, ?, NULL, ?, 'benchmark_suite', ?, 'Migrated from parse11', 'medium', '0', ?, ?)",
            ('chip_model_benchmarks', str(new_id), '_batch', '_batch', matched,
             d.get('source_url', f'https://huggingface.co/{mid}'), now,
             'Benchmark migration from parse11'))

        ins += 1
        if ins % 500 == 0:
            print(f'  benchmarks: {ins}/{len(bench_rows)}')

    db.commit()
    print(f'Benchmarks: {ins} inserted, {skip} skipped')

    # ---- COMPAT ----
    comp_rows = p11.execute('SELECT * FROM model_chip_compat').fetchall()
    print(f'\nParse11 compat: {len(comp_rows)}')

    ins_c = 0; skip_c = 0
    for row in comp_rows:
        d = dict(row)
        cm = (d.get('chip_model') or '').strip()
        mid = (d.get('model_id') or '').strip()
        if not cm or not mid:
            skip_c += 1; continue
        if mid not in model_ids:
            skip_c += 1; continue

        matched = cm
        for cn in chip_names:
            if cm.lower().replace(' ','') in cn.lower().replace(' ','') or \
               cn.lower().replace(' ','') in cm.lower().replace(' ',''):
                matched = cn; break

        cs = d.get('compat_status') or 'community'
        dup = db.execute(
            'SELECT id FROM chip_model_compatibility WHERE chip_model=? AND model_id=? AND compat_status=?',
            (matched, mid, cs)).fetchone()
        if dup:
            skip_c += 1; continue

        fields = {
            'chip_model': matched,
            'model_id': mid,
            'compat_status': cs,
            'framework': d.get('framework') or '',
            'precision': d.get('precision') or '',
            'notes': d.get('notes') or '',
        }
        fields = {k: v for k, v in fields.items() if v and v.strip()}
        if 'chip_model' not in fields or 'model_id' not in fields:
            skip_c += 1; continue

        cols = list(fields.keys()); vals = list(fields.values())
        db.execute(
            f"INSERT INTO chip_model_compatibility ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})",
            vals)
        new_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

        db.execute(
            "INSERT INTO field_provenance (table_name, row_id, field_name, field_label, old_value, new_value, "
            "source_type, source_url, source_detail, confidence, is_official, updated_at, notes) "
            "VALUES (?, ?, ?, ?, NULL, ?, 'community', '', 'Migrated from parse11', 'medium', '0', ?, ?)",
            ('chip_model_compatibility', str(new_id), 'chip_model', 'chip_model', matched,
             now, 'Compat migration from parse11'))

        ins_c += 1
        if ins_c % 500 == 0:
            print(f'  compat: {ins_c}/{len(comp_rows)}')

    db.commit()
    print(f'Compat: {ins_c} inserted, {skip_c} skipped')

    # ---- FINAL ----
    print('\n=== Final state ===')
    for t in ['chips','models','chip_model_benchmarks','chip_model_compatibility','field_provenance']:
        c = db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'  {t}: {c}')

    db.close(); p11.close()

if __name__ == '__main__':
    main()
