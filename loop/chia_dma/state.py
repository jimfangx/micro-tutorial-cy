"""Native populations, CHIA numerical history, and a small proposal recovery ledger."""

from pathlib import Path
import json
import uuid
from chia.database.sqlite_node import SQLiteNode
from skydiscover.config import OpenEvolveNativeDatabaseConfig
from skydiscover.search.base_database import Program
from skydiscover.search.openevolve_native.database import OpenEvolveNativeDatabase
from .artifacts import read_json, write_json
from .config import digest, frozen


class Population(OpenEvolveNativeDatabase):
    """Public database adapter: recover original selections and pin admission islands."""

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
        # Never mutate a shared parent while concurrent controller tasks use it.
        parent = Program(**parent.to_dict())
        parent.metadata["request_id"] = request["id"]
        return parent, context

    def add(self, program, iteration=None, **kwargs):
        if program.metrics.get("validity") != 1:
            raise ValueError("Only fully valid candidates may enter the population")
        payload = json.loads(program.solution)
        cid = payload["proposal_id"]
        request = self.owner.request(cid)
        program.metadata.update(candidate_id=cid, source_hash=digest(payload["source"]))
        kwargs.setdefault("target_island", request["island"])
        result = super().add(program, iteration=iteration, **kwargs)
        if (
            not kwargs.get("_is_migration")
            and kwargs["target_island"] == request["island"]
        ):
            self.owner.sql.execute(
                "UPDATE candidates SET program_id=? WHERE id=?", (program.id, cid)
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
        self.sql = SQLiteNode(str(self.run / "metrics.db"), require_colocated=False)
        self.sql.init_schema("""CREATE TABLE IF NOT EXISTS candidates
          (id TEXT PRIMARY KEY,parent TEXT,island INTEGER,sequence INTEGER,backend TEXT,model TEXT,metrics TEXT,program_id TEXT);
          CREATE TABLE IF NOT EXISTS benchmarks
          (candidate TEXT,name TEXT,metrics TEXT,PRIMARY KEY(candidate,name));""")
        self.recovery = self.pending()

    def request(self, cid):
        return read_json(self.run / "candidates" / cid / "request.json")

    def requests(self):
        return sorted(
            (read_json(p) for p in (self.run / "candidates").glob("*/request.json")),
            key=lambda r: r["sequence"],
        )

    def reserve(self, parent, inspirations, *, cid=None, kind="search"):
        request = {
            "id": cid or "candidate-" + uuid.uuid4().hex,
            "kind": kind,
            "sequence": max((r["sequence"] for r in self.requests()), default=0) + 1,
            "parent": parent.to_dict(),
            "inspirations": [p.to_dict() for p in inspirations],
            "island": parent.metadata["island"],
            "backend": self.cfg["agent"]["backend"],
        }
        write_json(self.run / "candidates" / request["id"] / "request.json", request)
        return request

    def result(self, cid):
        value = self.sql.query_value(
            "SELECT metrics FROM candidates WHERE id=?", (cid,)
        )
        return json.loads(value) if value else None

    def pending(self):
        return [
            r
            for r in self.requests()
            if r["kind"] == "search"
            and r["id"] not in self.admitted
            and (self.result(r["id"]) or {}).get("validity") != 0
        ]

    def record(self, cid, metrics, vector, program_id=""):
        request = self.request(cid)
        provenance = read_json(self.run / "candidates" / cid / "proposal.json")
        self.sql.transaction(
            [
                (
                    "INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?,?,?,?)",
                    (
                        cid,
                        (
                            None
                            if request["kind"] == "seed"
                            else request["parent"]["metadata"].get(
                                "candidate_id", request["parent"]["id"]
                            )
                        ),
                        request["island"],
                        request["sequence"],
                        provenance.get("backend", request["backend"]),
                        provenance.get("model", self.cfg["agent"]["model"]),
                        json.dumps(metrics),
                        program_id,
                    ),
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
        name = f"{iteration:08d}-" + uuid.uuid4().hex[:8]
        temp = root / ("." + name)
        temp.mkdir()
        self.population.save(
            str(temp / "population"), iteration=self.population.last_iteration
        )
        # These small flags define exactly which proposal/history records existed
        # at this native checkpoint, including concurrent unfinished generations.
        requests = {
            r["id"]: {
                "evaluated": self.result(r["id"]) is not None,
                "generated": (
                    self.run / "candidates" / r["id"] / "proposal.json"
                ).exists(),
            }
            for r in self.requests()
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


def fork(origin, checkpoint, target):
    """Copy one native checkpoint and only history that existed at that point."""
    import shutil
    import sqlite3

    if read_json(origin / "run.json").get("format") != 3:
        raise ValueError("Use the previous loop revision for legacy checkpoints")
    cp = origin / "checkpoints" / checkpoint
    envelope = read_json(cp / "state.json")
    known = envelope["requests"]

    def ignore(folder, names):
        folder = Path(folder)
        if folder == origin:
            return [
                n
                for n in names
                if n
                in (
                    "checkpoints",
                    "cache",
                    "artifacts",
                    "deployment",
                    "work",
                    "ray",
                    ".writer.lock",
                )
                or n.startswith("metrics.db")
            ]
        if folder == origin / "candidates":
            return [n for n in names if n not in known]
        if (
            folder.parent == origin / "candidates"
            and not known[folder.name]["generated"]
        ):
            return [n for n in names if n != "request.json"]
        return []

    shutil.copytree(origin, target, ignore=ignore)
    shutil.copytree(cp, target / "checkpoints" / checkpoint)
    for cid, flags in known.items():
        if not flags["generated"]:
            (target / "candidates" / cid).chmod(0o755)
    with sqlite3.connect(
        "file:" + str(origin / "metrics.db") + "?mode=ro", uri=True
    ) as src, sqlite3.connect(target / "metrics.db") as dst:
        src.backup(dst)
        keep = [cid for cid, flags in known.items() if flags["evaluated"]]
        for table, column in [("candidates", "id"), ("benchmarks", "candidate")]:
            dst.execute(
                f'DELETE FROM {table} WHERE {column} NOT IN ({",".join("?" for _ in keep)})',
                keep,
            )
        for cid in keep:
            if cid not in envelope["admitted"]:
                dst.execute("UPDATE candidates SET program_id=NULL WHERE id=?", (cid,))
    meta = read_json(target / "run.json")
    meta.update(
        run_id=target.name, forked_from={"run": origin.name, "checkpoint": checkpoint}
    )
    write_json(target / "run.json", meta)
    write_json(target / "latest.json", {"checkpoint": checkpoint})
