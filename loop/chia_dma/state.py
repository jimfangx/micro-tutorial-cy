"""Native populations plus one SQL record per proposal and numerical history."""

from pathlib import Path
import json
import shutil
import sqlite3
import uuid
import ray
from chia.base.ChiaFunction import get
from chia.database.sqlite_node import SQLiteNode
from skydiscover.config import OpenEvolveNativeDatabaseConfig
from skydiscover.search.base_database import Program
from skydiscover.search.openevolve_native.database import OpenEvolveNativeDatabase
from .artifacts import read_json, write_json, validate_bundle
from .config import digest, frozen


def proposal(sql, cid, *, remote=False):
    query = sql.query_one
    args = (
        "SELECT source,source_hash,generation,backend,model FROM proposals WHERE id=?",
        (cid,),
    )
    row = get(query.chia_remote(*args)) if remote else query(*args)
    if not row or row["source"] is None:
        return None
    return {
        "id": cid,
        **row,
        "source": json.loads(row["source"]),
        "generation": json.loads(row["generation"]),
    }


def complete(sql, cid, source, generation, *, remote=False):
    """Commit the complete proposal before generation returns; never replace it."""
    validate_bundle(source)
    args = (
        "UPDATE proposals SET source=?,source_hash=?,generation=?,backend=?,model=? WHERE id=? AND source IS NULL",
        (
            json.dumps(source),
            digest(source),
            json.dumps(generation),
            generation.get("backend"),
            generation.get("model"),
            cid,
        ),
    )
    if remote:
        get(sql.execute.chia_remote(*args))
    else:
        sql.execute(*args)
    saved = proposal(sql, cid, remote=remote)
    if saved is None or saved["source"] != source:
        raise ValueError("Missing request or immutable proposal ID reused")
    return saved


class Population(OpenEvolveNativeDatabase):
    """Public sample/add hooks preserve selections and pin admission islands."""

    def __init__(self, owner, config):
        self.owner = owner
        super().__init__("openevolve_native", config)

    def sample(self, num_context_programs=4, **kwargs):
        if self.owner.recovery:
            request = self.owner.recovery.pop(0)
            parent = Program(**request["parent"])
            context = [Program(**p) for p in request["inspirations"]]
        else:
            parent, context = super().sample(num_context_programs, **kwargs)
            request = self.owner.reserve(parent, context)
        parent = Program(**parent.to_dict())
        parent.metadata["request_id"] = request["id"]
        return parent, context

    def add(self, program, iteration=None, **kwargs):
        if program.metrics.get("validity") != 1:
            raise ValueError("Only fully valid candidates may enter the population")
        cid = json.loads(program.solution)["proposal_id"]
        request = self.owner.request(cid)
        saved = self.owner.proposal(cid)
        program.metadata.update(candidate_id=cid, source_hash=saved["source_hash"])
        kwargs.setdefault("target_island", request["island"])
        result = super().add(program, iteration=iteration, **kwargs)
        if (
            not kwargs.get("_is_migration")
            and kwargs["target_island"] == request["island"]
        ):
            self.owner.sql.execute(
                "UPDATE proposals SET program_id=? WHERE id=?", (program.id, cid)
            )
        self.owner.admitted.add(cid)
        return result


class State:
    def __init__(self, cfg, run):
        self.cfg, self.run = cfg, Path(run)
        self.run.mkdir(parents=True, exist_ok=True)
        self.admitted = set()
        fields = OpenEvolveNativeDatabaseConfig.__dataclass_fields__
        config = OpenEvolveNativeDatabaseConfig(
            **{k: v for k, v in cfg["search"].items() if k in fields}
        )
        config.db_path = None
        self.population = Population(self, config)
        if (self.run / "latest.json").exists():
            cp = (
                self.run
                / "checkpoints"
                / read_json(self.run / "latest.json")["checkpoint"]
            )
            envelope = read_json(cp / "state.json")
            if envelope["frozen_hash"] != digest(frozen(cfg)):
                raise ValueError("Frozen experiment configuration changed")
            self.population.load(str(cp / "population"))
            self.admitted = set(envelope["admitted"])
        self.sql = SQLiteNode(
            str(self.run / "metrics.db"),
            require_colocated=False,
            pin_to_current_node=ray.is_initialized(),
        )
        self.sql.init_schema("""CREATE TABLE IF NOT EXISTS proposals
          (id TEXT PRIMARY KEY,parent TEXT,island INTEGER,sequence INTEGER,kind TEXT,
           request TEXT,source TEXT,source_hash TEXT,generation TEXT,backend TEXT,model TEXT,metrics TEXT,program_id TEXT);
          CREATE TABLE IF NOT EXISTS benchmarks
          (candidate TEXT,name TEXT,metrics TEXT,PRIMARY KEY(candidate,name));""")
        self.recovery = self.pending()

    def request(self, cid):
        return json.loads(
            self.sql.query_value("SELECT request FROM proposals WHERE id=?", (cid,))
        )

    def requests(self):
        return [
            json.loads(r["request"])
            for r in self.sql.query("SELECT request FROM proposals ORDER BY sequence")
        ]

    def reserve(self, parent, inspirations, *, cid=None, kind="search"):
        request = {
            "id": cid or "candidate-" + uuid.uuid4().hex,
            "kind": kind,
            "sequence": self.sql.query_value(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM proposals"
            ),
            "parent": parent.to_dict(),
            "inspirations": [p.to_dict() for p in inspirations],
            "island": parent.metadata["island"],
            "backend": self.cfg["agent"]["backend"],
        }
        self.sql.execute(
            "INSERT INTO proposals(id,parent,island,sequence,kind,request,backend) VALUES (?,?,?,?,?,?,?)",
            (
                request["id"],
                (
                    None
                    if kind == "seed"
                    else parent.metadata.get("candidate_id", parent.id)
                ),
                request["island"],
                request["sequence"],
                kind,
                json.dumps(request),
                request["backend"],
            ),
        )
        return request

    def proposal(self, cid):
        return proposal(self.sql, cid)

    def complete(self, cid, source, generation):
        return complete(self.sql, cid, source, generation)

    def result(self, cid):
        value = self.sql.query_value("SELECT metrics FROM proposals WHERE id=?", (cid,))
        return json.loads(value) if value else None

    def benchmarks(self, cid):
        return {
            row["name"]: json.loads(row["metrics"])
            for row in self.sql.query(
                "SELECT name,metrics FROM benchmarks WHERE candidate=?", (cid,)
            )
        }

    def pending(self):
        return [
            r
            for r in self.requests()
            if r["kind"] == "search"
            and r["id"] not in self.admitted
            and (self.result(r["id"]) or {}).get("validity") != 0
        ]

    def record(self, cid, metrics, vector, program_id=""):
        self.sql.transaction(
            [
                (
                    "UPDATE proposals SET metrics=?,program_id=? WHERE id=?",
                    (json.dumps(metrics), program_id, cid),
                ),
                *[
                    (
                        "INSERT OR REPLACE INTO benchmarks VALUES (?,?,?)",
                        (cid, name, json.dumps(row)),
                    )
                    for name, row in vector.items()
                ],
            ]
        )

    def checkpoint(self, iteration=0):
        root = self.run / "checkpoints"
        root.mkdir(exist_ok=True)
        sequence = 1 + max(
            (int(p.name.split("-")[0]) for p in root.glob("[0-9]*")), default=-1
        )
        name = f"{sequence:08d}-" + uuid.uuid4().hex[:8]
        temp = root / ("." + name)
        temp.mkdir()
        self.population.save(
            str(temp / "population"), iteration=self.population.last_iteration
        )
        requests = {
            r["id"]: {"generated": r["generated"], "evaluated": r["evaluated"]}
            for r in self.sql.query(
                "SELECT id,source IS NOT NULL AS generated,metrics IS NOT NULL AS evaluated FROM proposals"
            )
        }
        write_json(
            temp / "state.json",
            {
                "frozen_hash": digest(frozen(self.cfg)),
                "admitted": sorted(self.admitted),
                "requests": requests,
            },
        )
        temp.rename(root / name)
        write_json(self.run / "latest.json", {"checkpoint": name})
        return name

    def close(self):
        self.sql.wal_checkpoint()
        self.sql.close()


def fork(origin, checkpoint, target, *, candidate=None):
    """Keep the native population and only SQL history visible at this checkpoint."""
    if target.resolve().is_relative_to(origin.resolve()):
        raise ValueError("Fork destination must be outside the source experiment")
    if candidate:
        history = sorted((origin / "checkpoints").glob("[!.]*/state.json"))
        checkpoint = next(
            (p.parent.name for p in history if candidate in read_json(p)["admitted"]),
            None,
        )
        if checkpoint is None:
            raise ValueError("No committed checkpoint contains candidate: " + candidate)
    checkpoint = checkpoint or read_json(origin / "latest.json")["checkpoint"]
    cp = origin / "checkpoints" / checkpoint
    envelope = read_json(cp / "state.json")
    known = envelope["requests"]
    shutil.copytree(
        origin,
        target,
        ignore=shutil.ignore_patterns(
            "checkpoints",
            "cache",
            "artifacts",
            "deployment",
            "work",
            ".writer.lock",
            "metrics.db*",
        ),
    )
    shutil.copytree(cp, target / "checkpoints" / checkpoint)
    with sqlite3.connect(
        "file:" + str(origin / "metrics.db") + "?mode=ro", uri=True
    ) as src, sqlite3.connect(target / "metrics.db") as dst:
        src.backup(dst)
        for row in dst.execute("SELECT id FROM proposals").fetchall():
            cid = row[0]
            flags = known.get(cid)
            if flags is None:
                dst.execute("DELETE FROM proposals WHERE id=?", (cid,))
                dst.execute("DELETE FROM benchmarks WHERE candidate=?", (cid,))
                continue
            if not flags["generated"]:
                dst.execute(
                    "UPDATE proposals SET source=NULL,source_hash=NULL,generation=NULL,model=NULL,backend=json_extract(request,'$.backend') WHERE id=?",
                    (cid,),
                )
            if not flags["evaluated"]:
                dst.execute("UPDATE proposals SET metrics=NULL WHERE id=?", (cid,))
                dst.execute("DELETE FROM benchmarks WHERE candidate=?", (cid,))
            if cid not in envelope["admitted"]:
                dst.execute("UPDATE proposals SET program_id=NULL WHERE id=?", (cid,))
    meta = read_json(target / "run.json")
    meta.update(
        run_id=target.name, forked_from={"run": origin.name, "checkpoint": checkpoint}
    )
    write_json(target / "run.json", meta)
    write_json(target / "latest.json", {"checkpoint": checkpoint})
