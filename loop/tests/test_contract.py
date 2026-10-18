"""Fixed-output, isolation, and recovery tests; fixture data comes from real RTL."""
from pathlib import Path
import copy
import json
import random
import pytest

from chia_dma.config import ALLOWLIST, digest, load
from chia_dma.artifacts import archive_proposal, read_json, source_bundle, validate_bundle
from chia_dma.metrics import aggregate, parse


@pytest.fixture
def cfg(tmp_path):
    c = load()
    c['storage'] = str(tmp_path / 'storage')
    return c


@pytest.fixture
def records(cfg):
    data = json.loads((Path(__file__).parent / 'fixtures/rtl-measurements.json').read_text())
    result = {}
    for row in data['embench']:
        name = f'embench:{row["mode"]}:{row["benchmark"]}'
        text = 'EMBENCH ' + ' '.join(f'{k}={v}' for k,v in row.items() if isinstance(v,int))
        result[name] = parse(name, {'success': True, 'returncode': 0, 'log':text, 'out':''}, cfg)
    text = '\n'.join('MICRO '+' '.join(f'{k}={v}' for k,v in row.items()) for row in data['microbench'])
    result['microbench'] = parse('microbench', {'success':True,'returncode':0,'log':text+'\nDMA microbenchmark PASS','out':''},cfg)
    return result


def test_real_rtl_reference_and_group_weighted_geomean(cfg, records):
    score, vector = aggregate(records, records, cfg, {p: 'source' for p in ALLOWLIST})
    assert score['combined_score'] == 1
    assert len([k for k in vector if k.startswith('embench:')]) == len(records)//2
    faster = copy.deepcopy(records)
    for name,row in faster.items():
        if name.startswith('embench:dma:'):
            row['benchmark_cycles'] /= 4
    cfg['fitness'].update(embench_weight=1,microbench_weight=1)
    score, _ = aggregate(faster, records, cfg, {p:'source' for p in ALLOWLIST})
    assert score['combined_score'] == pytest.approx(2)


def test_activity_change_missing_workload_and_wrong_output_rejected(cfg, records):
    key = next(k for k,r in records.items() if k.startswith('embench:dma:') and r['offloaded'])
    for field in ('bytes','offloaded','benchmark_result'):
        bad = copy.deepcopy(records)
        bad[key][field] += 1
        with pytest.raises(ValueError):
            aggregate(bad, records, cfg, {p:'source' for p in ALLOWLIST})
    bad = copy.deepcopy(records)
    del bad[key]
    with pytest.raises(ValueError,match='Full Embench'):
        aggregate(bad, records, cfg, {p:'source' for p in ALLOWLIST})


def test_failed_or_incomplete_simulation_never_yields_score(cfg):
    for result in ({'success':False,'returncode':-1}, {'success':True,'returncode':0,'log':'EMBENCH result=0','out':''}):
        with pytest.raises(ValueError):
            parse('embench:dma:wikisort',result,cfg)
    with pytest.raises(ValueError):
        parse('microbench',{'success':True,'returncode':0,'log':'DMA microbenchmark PASS','out':''},cfg)


def test_proposal_commit_is_immutable_and_has_exact_allowlist(tmp_path):
    bundle = {p:'original\n' for p in ALLOWLIST}
    modified = dict(bundle)
    modified[ALLOWLIST[0]] += '// proposal\n'
    archive_proposal(tmp_path,'candidate',modified,bundle,{'parent_id':'seed','iteration':1})
    saved = read_json(tmp_path/'candidates/candidate/proposal.json')
    assert saved['source_hash'] == digest(modified)
    assert source_bundle(tmp_path/'candidates/candidate/rtl') == modified
    with pytest.raises(ValueError):
        archive_proposal(tmp_path,'candidate',bundle,bundle,{})
    with pytest.raises(ValueError):
        validate_bundle({**bundle,'tests/cheat.c':'bad'})


@pytest.fixture(scope='module')
def ray_runtime():
    import ray
    ray.init(num_cpus=2,include_dashboard=False,_node_ip_address='127.0.0.1')
    yield
    ray.shutdown()


def test_native_islands_migration_checkpoint_restores_rng_and_inflight(cfg,tmp_path,ray_runtime):
    from chia_dma.state import State
    from skydiscover.search.base_database import Program
    cfg['search']['migration_interval']=2
    cfg['search']['feature_dimensions']=['source_bytes','large_copy_bandwidth']
    run = tmp_path/'run'
    state = State(cfg,run)
    for i in range(8):
        state.population.add(Program(id=str(i),solution=json.dumps({p:f'source {i}' for p in ALLOWLIST}),
                             metrics={'combined_score':1+i/10,'source_bytes':20+i,'large_copy_bandwidth':i+1},
                             metadata={'island':i%2}),iteration=i,target_island=i%2)
    assert all(state.population.island_feature_maps)
    assert state.population.last_migration_generation > 0
    state.tasks={'pending':{'id':'pending','stage':'proposed','source_hash':'saved'}}
    cp=state.checkpoint('proposed')
    expected_rng=random.random()
    saved=[dict(m) for m in state.population.island_feature_maps]
    state.population.programs.clear()
    state.restore(cp)
    assert random.random()==expected_rng
    assert state.tasks['pending']['stage']=='proposed'
    assert state.population.island_feature_maps==saved
    assert state.population.best_program_id=='7'
    assert len(state.population.island_best_programs)==2
    state.close()


def test_backend_switch_and_historical_fork_preserve_native_history(cfg,tmp_path,ray_runtime):
    from chia_dma.state import State
    from chia_dma.artifacts import write_json, files
    from chia_dma.config import frozen
    from skydiscover.search.base_database import Program
    import loop as cli
    origin=tmp_path/'origin'
    state=State(cfg,origin)
    bundle={p:'seed source' for p in ALLOWLIST}
    state.population.add(Program(id='parent',solution=json.dumps(bundle),metrics={'combined_score':1,'source_bytes':1,'large_copy_bandwidth':1}),iteration=1,target_island=0)
    state.tasks={'parent':{'id':'parent','stage':'updated','iteration':1,'island':0,'metrics':{'combined_score':1}}}
    cp=state.checkpoint('historical')
    write_json(origin/'software/manifest.json',{'workloads':{}})
    write_json(origin/'run.json',{'run_id':'origin','seed':'frozen-seed','frozen_hash':digest(frozen(cfg))})
    state.population.add(Program(id='later',solution=json.dumps(bundle),metrics={'combined_score':2,'source_bytes':1,'large_copy_bandwidth':2}),iteration=2,target_island=0)
    state.checkpoint('later')
    state.close()
    before=files(origin/'checkpoints')
    cfg['agent']['backend']='claude'
    resumed=State(cfg,origin)
    assert resumed.population.best_program_id=='later'
    resumed.close()
    target=tmp_path/'fork'
    cli.fork(cfg,origin,cp,target)
    child=State(cfg,target)
    assert child.population.best_program_id=='parent'
    assert 'later' not in child.population.programs
    assert child.population.last_iteration==1
    assert files(origin/'checkpoints')==before
    child.close()


def test_invalid_evaluation_is_retained_but_not_admitted(cfg,tmp_path,ray_runtime,monkeypatch):
    from chia_dma.state import State
    from chia_dma import search
    from skydiscover.search.base_database import Program
    from skydiscover.evaluation.evaluation_result import EvaluationResult
    import asyncio
    state=State(cfg,tmp_path/'invalid')
    bundle={p:'source' for p in ALLOWLIST}
    state.population.add(Program(id='parent',solution=json.dumps(bundle),metrics={'combined_score':1,'source_bytes':1,'large_copy_bandwidth':1}),target_island=0)
    task={'id':'bad','iteration':1,'island':0,'parent_id':'parent','parent_source':bundle,'inspiration_ids':[],'stage':'proposed','prompt':'fixed'}
    state.tasks['bad']=task
    archive_proposal(state.run,'bad',bundle,bundle,task)
    class Incorrect:
        def __init__(self,*args,**kwargs): pass
        async def evaluate_program(self,*args):
            return EvaluationResult({'combined_score':1000000,'validity':0})
    monkeypatch.setattr(search,'DMAEvaluator',Incorrect)
    asyncio.run(search.process(cfg,state,tmp_path/'seed',task))
    assert set(state.population.programs)=={'parent'}
    assert state.population.best_program_id=='parent'
    assert state.tasks['bad']['stage']=='updated'
    assert (state.run/'candidates/bad/proposal.json').exists()
    state.close()


def test_all_three_real_chia_agent_classes_are_configurable(cfg):
    from chia_dma.worker import make_agent
    names={'antigravity':'AntigravityLLM','claude':'ClaudeCodeLLM','codex':'CodexLLM'}
    for backend,classname in names.items():
        cfg['agent']['backend']=backend
        agent=make_agent(cfg)
        assert type(agent).__name__==classname
        assert agent.timeout_seconds==cfg['agent']['timeout_seconds']


def test_allowlisted_file_cannot_change_fixed_soc_mixin_or_l1():
    from chia_dma.artifacts import validate_integration
    source={p:'source' for p in ALLOWLIST}
    source[ALLOWLIST[1]]='class Accelerator {\n  io.mem.req.valid := false.B\n}\nclass WithRoCCDMA(x: Int) extends Config(fixed)\n'
    candidate=dict(source)
    candidate[ALLOWLIST[1]]=candidate[ALLOWLIST[1]].replace('Config(fixed)','Config(cheating)')
    with pytest.raises(ValueError,match='integration'):
        validate_integration(candidate,source)
    candidate[ALLOWLIST[1]]=source[ALLOWLIST[1]].replace('false.B','true.B')
    with pytest.raises(ValueError,match='L1'):
        validate_integration(candidate,source)
    validate_integration(source,source)


def test_parallel_collection_unwraps_cache_and_profiler_callbacks(tmp_path,ray_runtime):
    import ray
    from chia.base.ChiaFunction import ChiaFunction
    from chia.base.cache import start_cache,stop_cache
    from chia.trace.profiler import start_collector,stop_collector,reset_profiler
    from chia_dma.evaluator import resolve
    policy=tmp_path/'cache.yaml';policy.write_text('cache:\n  measured: true\n')
    start_collector(log_dir=str(tmp_path/'profile'))
    reset_profiler()
    start_cache(1,str(tmp_path/'cache'),units='MB',yaml_path=str(policy))
    @ChiaFunction(num_cpus=0.1)
    def measured(value):
        return {'cycles':value}
    try:
        refs=[measured.chia_remote(i,_chia_tag=f'run/candidate/workload/{i}') for i in (10,20)]
        assert resolve(refs,timeout=30)==[{'cycles':10},{'cycles':20}]
    finally:
        stop_cache()
        stop_collector()
        reset_profiler()
