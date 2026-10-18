"""CHIA SQLite metadata + native OpenEvolve checkpoints, atomically published."""
from pathlib import Path
import json
import random
import shutil
import uuid
from chia.database.sqlite_node import SQLiteNode
from skydiscover.config import OpenEvolveNativeDatabaseConfig
from skydiscover.search.openevolve_native.database import OpenEvolveNativeDatabase
from .artifacts import archive_proposal, readonly, read_json, sync_tree, write_json
from .config import digest, frozen

SCHEMA = '''
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, config TEXT NOT NULL, seed TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidates
 (id TEXT PRIMARY KEY, parent TEXT, island INTEGER, iteration INTEGER, source_hash TEXT,
  proposal TEXT, backend TEXT, model TEXT, status TEXT, metrics TEXT);
CREATE TABLE IF NOT EXISTS stages
 (candidate TEXT, stage TEXT, record TEXT NOT NULL, PRIMARY KEY(candidate,stage));
CREATE TABLE IF NOT EXISTS benchmarks
 (candidate TEXT, name TEXT, metrics TEXT NOT NULL, PRIMARY KEY(candidate,name));
CREATE TABLE IF NOT EXISTS checkpoints
 (id TEXT PRIMARY KEY, stage TEXT NOT NULL, path TEXT NOT NULL);
'''


def tuples(value):
    return tuple(tuples(v) for v in value) if isinstance(value, list) else value


class State:
    def __init__(self, cfg, run, *, restore=True):
        self.cfg, self.run = cfg, Path(run)
        self.run.mkdir(parents=True, exist_ok=True)
        self.sql = SQLiteNode(str(self.run / 'metrics.db'), require_colocated=False,
                              connect_opts={'synchronous': 'FULL'})
        self.sql.init_schema(SCHEMA)
        search = cfg['search']
        valid = OpenEvolveNativeDatabaseConfig.__dataclass_fields__
        config = OpenEvolveNativeDatabaseConfig(**{k: v for k, v in search.items() if k in valid})
        # All persistence goes through complete checkpoint directories, avoiding
        # native add()'s non-atomic autosaves into a live population directory.
        config.db_path = None
        self.population = OpenEvolveNativeDatabase('rocc_dma', config)
        self.tasks = {}
        self.sequence = 0
        self.next_iteration = 1
        if restore and (self.run / 'latest.json').exists():
            self.restore(read_json(self.run / 'latest.json')['checkpoint'])
        else:
            random.seed(search['random_seed'])

    def restore(self, checkpoint):
        checkpoint = self.run / 'checkpoints' / checkpoint
        state = read_json(checkpoint / 'state.json')
        if state['frozen_hash'] != digest(frozen(self.cfg)):
            raise ValueError('Frozen evaluation/search configuration changed; use a new experiment')
        if (checkpoint / 'metrics.db').exists():
            self.sql.wal_checkpoint()
            shutil.copyfile(checkpoint / 'metrics.db', self.run / 'metrics.db')
        self.population.load(str(checkpoint / 'population'))
        self.tasks = state['tasks']
        self.sequence = state['sequence']
        self.next_iteration = state['next_iteration']
        random.setstate(tuples(state['random_state']))

    def checkpoint(self, stage):
        # Native migration/empty-island seeding may create equivalent programs.
        # Link those native IDs to immutable sources without another LLM call.
        for program in self.population.programs.values():
            bundle = json.loads(program.solution)
            proposal = self.run / 'candidates' / program.id / 'proposal.json'
            if not proposal.exists():
                archive_proposal(self.run, program.id, bundle, bundle,
                                 {'id': program.id, 'parent_id': program.parent_id,
                                  'island': program.metadata.get('island', 0),
                                  'iteration': program.iteration_found, 'native_copy': True})
            self.sql.execute('''INSERT OR IGNORE INTO candidates
                (id,parent,island,iteration,source_hash,proposal,status,metrics)
                VALUES (?,?,?,?,?,?,?,?)''',
                (program.id, program.parent_id, program.metadata.get('island', 0),
                 program.iteration_found, digest(bundle), str(proposal.parent),
                 'updated', json.dumps(program.metrics)))
        self.sequence += 1
        name = f'{self.sequence:08d}-{stage}'
        root = self.run / 'checkpoints'
        root.mkdir(exist_ok=True)
        temp = root / ('.' + uuid.uuid4().hex)
        temp.mkdir()
        self.population.save(str(temp / 'population'), iteration=self.population.last_iteration)
        write_json(temp / 'state.json', {
            'sequence': self.sequence, 'tasks': self.tasks, 'next_iteration': self.next_iteration,
            'random_state': random.getstate(), 'frozen_hash': digest(frozen(self.cfg)),
            'population_iteration': self.population.last_iteration})
        # CHIA's WAL API supplies the database snapshot; native save() supplies
        # population serialization. Neither depends on the LRU output cache.
        if self.sql.wal_checkpoint()['busy']:
            raise RuntimeError('Cannot checkpoint SQLite while a writer is active')
        shutil.copyfile(self.run / 'metrics.db', temp / 'metrics.db')
        sync_tree(temp)
        final = root / name
        temp.rename(final)
        readonly(final)
        self.sql.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?,?)', (name, stage, str(final)))
        write_json(self.run / 'latest.json', {'checkpoint': name})
        return name

    def stage(self, task, stage, record=None):
        record = record or {}
        self.sql.transaction([
            ('INSERT OR REPLACE INTO stages VALUES (?,?,?)', (task['id'], stage, json.dumps(record))),
            ('''INSERT INTO candidates(id,parent,island,iteration,status,backend,model)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status''',
             (task['id'], task.get('parent_id'), task.get('island', 0), task.get('iteration', 0), stage,
              task.get('backend', self.cfg['agent']['backend']), task.get('model', self.cfg['agent']['model'])))])
        if stage == 'proposed':
            proposal = self.run / 'candidates' / task['id'] / 'proposal.json'
            data = read_json(proposal)
            self.sql.execute('UPDATE candidates SET source_hash=?,proposal=? WHERE id=?',
                             (data['source_hash'], str(proposal.parent), task['id']))
        task['stage'] = stage
        self.checkpoint(stage)

    def evaluated(self, task, metrics, vector):
        proposal = read_json(self.run / 'candidates' / task['id'] / 'proposal.json')
        statements = [(
            '''UPDATE candidates SET source_hash=?, proposal=?, metrics=?, status='evaluated',
                backend=?,model=? WHERE id=?''',
            (proposal['source_hash'], str(self.run / 'candidates' / task['id']), json.dumps(metrics),
             proposal.get('backend'), proposal.get('model'), task['id']))]
        for name, row in vector.items():
            statements.append(('INSERT OR REPLACE INTO benchmarks VALUES (?,?,?)', (task['id'], name, json.dumps(row))))
        self.sql.transaction(statements)
        task['metrics'], task['vector'] = metrics, vector
        self.stage(task, 'evaluated', metrics)

    def close(self):
        self.sql.wal_checkpoint()
        self.sql.close()
