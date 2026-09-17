import json
import sqlite3
from pathlib import Path

import pytest

from chip_model.pipeline import run_history as history

SCHEMA = (Path(__file__).parents[1] / 'schema.sql').read_text(encoding='utf-8')

def make_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO chips (id,chip_model,vendor,vram_gb) VALUES (1,'Test GPU','Vendor','80')")
        db.execute("INSERT INTO field_provenance (table_name,row_id,field_name,new_value,source_url,updated_at) VALUES ('chips','1','vram_gb','80','https://old.example/spec','2026-01-01')")
    return path

def session(base, name):
    path = make_db(base.parent / 'test_runs' / name / 'data.db')
    (path.parent / 'test_mode.json').write_text(json.dumps({'mode':'test','db_path':str(path)}))
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO update_runs (id,run_type,mode,status,started_at,created_at) VALUES (1,'data_agent_cycle','test','awaiting_publish','2026-09-17','2026-09-17')")
    return path

def candidate(path):
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO extraction_candidates (id,cycle_run_id,owner_skill,target_table,entity_key_json,field_name,proposed_value,evidence_text,source_url,status,extractor_version,created_at) VALUES (1,1,'chip-basic','chips','{\"id\":1}','vram_gb','94','94GB','https://new.example/spec','ready_to_publish','v1','2026-09-17')")

def test_sessions_are_distinct_and_invalid_paths_rejected(tmp_path):
    base=make_db(tmp_path/'data.db')
    session(base,'session-a'); session(base,'session-b')
    result=history.list_runs(base,mode='test')
    assert result['total']==2
    assert len({r['key'] for r in result['runs']})==2
    assert history.run_detail(base,'session-a',1)['session']=='session-a'
    with pytest.raises(ValueError): history.run_detail(base,'../outside',1)

def test_old_value_is_explicit_reference_for_legacy_candidates(tmp_path):
    base=make_db(tmp_path/'data.db');path=session(base,'session-a');candidate(path)
    row=history.run_detail(base,'session-a',1)['field_changes'][0]
    assert row['old_value']=='80'
    assert row['old_value_basis']=='current_reference'
    assert row['old_sources'][0]['source_url']=='https://old.example/spec'
    assert row['proposed_value']=='94'
    assert row['published'] is False

def test_baseline_snapshot_survives_later_entity_changes(tmp_path):
    base=make_db(tmp_path/'data.db');path=session(base,'session-a');candidate(path)
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        history.capture_candidate_baseline(db,1)
        db.execute("UPDATE chips SET vram_gb='999' WHERE id=1")
    row=history.run_detail(base,'session-a',1)['field_changes'][0]
    assert row['old_value']=='80'
    assert row['old_value_basis']=='candidate_snapshot'
    assert row['old_sources'][0]['source_url']=='https://old.example/spec'

def test_reads_all_source_phases_but_not_other_cycles(tmp_path):
    base=make_db(tmp_path/'data.db');path=session(base,'session-a')
    with sqlite3.connect(path) as db:
        for rid in (2,3,4):
            db.execute("INSERT INTO update_runs (id,run_type,mode,status,started_at,created_at) VALUES (?,'source_refresh','test','success','2026-09-17','2026-09-17')",(rid,))
            db.execute("INSERT INTO source_checks (run_id,requested_url,outcome,checked_at) VALUES (?,?,'new','2026-09-17')",(rid,f'https://example.org/{rid}'))
        for jid,rid in ((1,2),(2,3)):
            db.execute("INSERT INTO data_agent_jobs (cycle_run_id,dedupe_key,job_type,skill_name,status,input_json,scheduled_for,created_at,updated_at) VALUES (1,?,'agent_extract','chip-basic','succeeded',?,'2026-09-17','2026-09-17','2026-09-17')",(str(jid),json.dumps({'source_run_id':rid})))
    data=history.run_detail(base,'session-a',1)
    assert {s['run_id'] for s in data['sources']}=={2,3}

def test_bad_session_does_not_break_history(tmp_path):
    base=make_db(tmp_path/'data.db');session(base,'session-a')
    bad=base.parent/'test_runs'/'bad';bad.mkdir();(bad/'test_mode.json').write_text('{')
    assert history.list_runs(base,mode='test')['total']==1

def test_publication_uses_recorded_old_value_not_current_value(tmp_path):
    base=make_db(tmp_path/'data.db');path=session(base,'session-a');candidate(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE chips SET vram_gb='94'")
        db.execute("UPDATE extraction_candidates SET status='published'")
        db.execute("INSERT INTO candidate_publications (cycle_run_id,target_table,candidate_ids_json,status,old_values_json,new_values_json,source_url,validation_json,started_at) VALUES (1,'chips','[1]','success','{\"vram_gb\":\"80\"}','{\"vram_gb\":\"94\"}','https://new.example/spec','{}','2026-09-17')")
    row=history.run_detail(base,'session-a',1)['field_changes'][0]
    assert row['published'] is True
    assert row['old_value']=='80'
    assert row['old_value_basis']=='publication_record'

def test_api_history_and_missing_session(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from chip_model.server import app
    base=make_db(tmp_path/'data.db');session(base,'session-a')
    monkeypatch.setenv('DATA_DB_PATH',str(base))
    client=TestClient(app)
    assert client.get('/api/v1/data-agent/runs').json()['total']==1
    assert client.get('/api/v1/data-agent/runs/session-a/1').json()['session']=='session-a'
    assert client.get('/api/v1/data-agent/runs/missing/1').status_code==404

def test_worker_runtime_logs_are_scoped_and_do_not_expose_credentials(tmp_path):
    base=make_db(tmp_path/'data.db');path=session(base,'session-a')
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO data_agent_jobs (id,cycle_run_id,dedupe_key,job_type,skill_name,status,scheduled_for,created_at,updated_at) VALUES (7,1,'j7','agent_extract','chip-basic','failed','2026-09-17','2026-09-17','2026-09-17')")
    (path.parent/'job-7-response.json').write_text(json.dumps({'model':'kimi-k2.6','usage':{'total_tokens':42},'api_key':'do-not-expose'}))
    (path.parent/'trace.jsonl').write_text(json.dumps({'stage':'model_response','job_id':7,'time':'2026-09-17','http_status':200,'api_key':'do-not-expose'})+'\n')
    data=history.run_detail(base,'session-a',1)
    assert data['parameters']['model']=='kimi-k2.6'
    assert data['runtime']['total_tokens']==42
    assert 'do-not-expose' not in json.dumps(data)

def test_pending_jobs_in_legacy_clone_can_capture_baseline(tmp_path):
    base=make_db(tmp_path/'data.db');path=session(base,'session-a');candidate(path)
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        db.execute('DROP TABLE candidate_baselines')
        history.capture_candidate_baseline(db,1)
    assert history.run_detail(base,'session-a',1)['field_changes'][0]['old_value_basis']=='candidate_snapshot'
