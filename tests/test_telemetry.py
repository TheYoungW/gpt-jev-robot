import copy
import json
import runpy
from pathlib import Path
import numpy as np
from gpt_jev_robot.sim import RobotSim
from gpt_jev_robot.telemetry import RobotTelemetry


def test_encoder_trace_units_persistence_and_sampling_do_not_change_physics(tmp_path):
    a=RobotSim(tmp_path/'a',telemetry=True)
    b=RobotSim(tmp_path/'b')
    try:
        a.gripper(True);b.gripper(True)
        np.testing.assert_array_equal(a.data.qpos,b.data.qpos)
        np.testing.assert_array_equal(a.data.qvel,b.data.qvel)
        r=a.telemetry.summary('l')
        assert r['units']['joints_8_and_9']==['m','m/s','N']
        assert r['pad_gap']['end'][0]<r['pad_gap']['start'][0]-.02
        samples=np.array(a.telemetry.rows)
        np.testing.assert_allclose(np.diff(samples[:,0]),.01,atol=1e-9)
        a.save()
        loaded=RobotSim(a.path,telemetry=True)
        try:
            np.testing.assert_array_equal(np.array(loaded.telemetry.rows),samples)
            loaded.step(.02)
            assert len(loaded.telemetry.rows)==len(samples)+2
        finally:loaded.close()
        # Object position changes cannot alter the robot-only feature extraction.
        previous=copy.deepcopy(r)
        a.data.qpos[a.model.joint('gold_free').qposadr[0]]+=1
        assert a.telemetry.summary('l')==previous
    finally:a.close();b.close()


def test_sampler_bounds_the_window_and_drops_future_records(tmp_path):
    s=RobotSim(tmp_path/'s',telemetry=True)
    try:
        row=np.array(s.telemetry.rows[-1])
        for k in range(1300):
            r=row.copy();r[0]=k*.01;s.telemetry.rows.append(r)
        s.telemetry.save(tmp_path/'samples.npz')
        restored=RobotTelemetry(s.model,tmp_path/'samples.npz',12.5)
        assert len(restored.rows)<=1201
        assert restored.rows[-1][0]<=12.5 and restored.rows[0][0]>=.5
    finally:s.close()


def test_paired_inputs_only_differ_by_sensor_data_and_keep_identical_options():
    b=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/benchmark_telemetry.py'))
    frozen={'choices':b['CHOICES'],'cases':[{'id':'C01','episode':'E01','state':{'visual_observation':'occluded','robot_feedback':{}},'mechanical_history':{'q':[0]}}]}
    original=copy.deepcopy(frozen)
    jobs=b['request_jobs'](frozen)
    for repeat in range(3):
        pair={j['group']:j for j in jobs if j['repeat']==repeat}
        a,c=pair['A'],pair['B'];state=copy.deepcopy(c['state']);state.pop('mechanical_history')
        assert a['state']==state
        assert a['candidates']==c['candidates'] and a['mapping']==c['mapping']
        assert set(a['mapping'].values())=={'retained','not_retained','observe'}
    assert frozen==original


def test_analysis_serializes_cluster_counts_and_scores_abstention(tmp_path):
    b=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/benchmark_telemetry.py'))
    labels=[{'case':'C01','episode':'E01','label':'retained'},
            {'case':'C02','episode':'E02','label':'not_retained'}]
    (tmp_path/'labels.json').write_text(json.dumps({'labels':labels}))
    rows=[]
    for case in labels:
        for g in ('A','B'):
            for repeat in range(3):
                choice='observe' if g=='A' else case['label']
                rows.append({'case':case['case'],'group':g,'status':'ok','raw_choice':choice,
                    'accepted_choice':choice,'low_confidence':False,'started_wall_time':0,
                    'completed_wall_time':1,'decision':{'usage':{'input_tokens':1},'latency_ms':1}})
    namespace=b['analyze'].__globals__
    namespace['RUN']=tmp_path;namespace['complete_results']=lambda:rows
    b['analyze']()
    result=json.loads((tmp_path/'summary.json').read_text())
    assert result['cluster_sign_test']['B_wins']==2
    assert result['groups']['A']['accuracy']==0
    assert result['groups']['B']['accuracy']==1
    assert result['B_minus_A']==1
