"""Trusted append-oriented experiment evidence, independent of agent workspaces."""
from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def pose(observation):
    value = observation["robot"]
    result = {key: float(value[key]) for key in ("x", "y", "heading")}
    if not all(math.isfinite(x) for x in result.values()):
        raise ValueError("Nonfinite measured pose")
    return result


class Archive:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "evidence.sqlite3", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, started REAL NOT NULL, ended REAL,
          status TEXT NOT NULL, mode TEXT NOT NULL, config TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS generations (
          id TEXT PRIMARY KEY, run_id TEXT NOT NULL, started REAL NOT NULL,
          ended REAL, status TEXT NOT NULL, summary TEXT);
        CREATE TABLE IF NOT EXISTS experiments (
          id TEXT PRIMARY KEY, generation_id TEXT NOT NULL, source TEXT NOT NULL,
          phase TEXT NOT NULL, started REAL NOT NULL, ended REAL,
          before_json TEXT NOT NULL, action_json TEXT NOT NULL,
          prediction_json TEXT NOT NULL, after_json TEXT, error REAL,
          status TEXT NOT NULL, reason TEXT);
        CREATE TABLE IF NOT EXISTS findings (
          id TEXT PRIMARY KEY, generation_id TEXT NOT NULL, created REAL NOT NULL,
          claim TEXT NOT NULL, status TEXT NOT NULL,
          conditions TEXT NOT NULL, evidence_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evaluations (
          id TEXT PRIMARY KEY, generation_id TEXT NOT NULL, created REAL NOT NULL,
          model_json TEXT NOT NULL, results_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS artifacts (
          id TEXT PRIMARY KEY, generation_id TEXT NOT NULL, created REAL NOT NULL,
          path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS experiments_recent ON experiments(status,phase,started DESC);
        CREATE INDEX IF NOT EXISTS experiments_generation ON experiments(generation_id,status);
        CREATE INDEX IF NOT EXISTS artifacts_recent ON artifacts(created DESC,id DESC);
        CREATE INDEX IF NOT EXISTS artifacts_content ON artifacts(sha256);
        CREATE INDEX IF NOT EXISTS artifacts_generation_path ON artifacts(generation_id,path);
        CREATE INDEX IF NOT EXISTS findings_recent ON findings(created DESC);
        CREATE INDEX IF NOT EXISTS findings_generation ON findings(generation_id);
        CREATE INDEX IF NOT EXISTS evaluations_recent ON evaluations(created DESC);
        CREATE INDEX IF NOT EXISTS evaluations_generation ON evaluations(generation_id);
        CREATE INDEX IF NOT EXISTS runs_recent ON runs(started DESC);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def start_run(self, mode, config):
        run_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", (run_id, time.time(), None, "running", mode, canonical(config)))
        self.db.commit()
        return run_id

    def end_run(self, run_id, status):
        self.db.execute("UPDATE runs SET status=?,ended=? WHERE id=?", (status, time.time(), run_id))
        self.db.commit()

    def start_generation(self, run_id):
        generation_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO generations VALUES(?,?,?,?,?,?)", (generation_id, run_id, time.time(), None, "running", None))
        self.db.commit()
        return generation_id

    def end_generation(self, generation_id, status, summary):
        self.db.execute("UPDATE generations SET status=?,ended=?,summary=? WHERE id=?", (status, time.time(), str(summary)[:8000], generation_id))
        self.db.commit()

    def begin_experiment(self, generation_id, source, phase, before, action, prediction):
        """Commit the prediction BEFORE executing the action."""
        experiment_id = uuid.uuid4().hex
        pose(before)
        self.db.execute("INSERT INTO experiments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            experiment_id, generation_id, source, phase, time.time(), None,
            canonical(before), canonical(action), canonical(prediction), None, None, "pending", None))
        self.db.commit()
        return experiment_id

    def finish_experiment(self, experiment_id, after, error, status="completed", reason=None):
        if error is not None and not math.isfinite(error):
            raise ValueError("Nonfinite prediction error")
        row = self.db.execute("SELECT status FROM experiments WHERE id=?", (experiment_id,)).fetchone()
        if not row or row["status"] != "pending":
            raise ValueError("Experiment missing or already finalized")
        self.db.execute("UPDATE experiments SET ended=?,after_json=?,error=?,status=?,reason=? WHERE id=?", (
            time.time(), canonical(after) if after is not None else None, error, status, reason, experiment_id))
        self.db.commit()

    def experiments(self, limit=200, phase=None):
        limit = max(1, min(int(limit), 2000))
        query = "SELECT * FROM experiments WHERE status='completed'"
        args = []
        if phase is not None:
            query += " AND phase=?"
            args.append(phase)
        query += " ORDER BY started DESC LIMIT ?"
        args.append(limit)
        result = []
        for row in self.db.execute(query, args):
            item = dict(row)
            for key in ("before_json", "action_json", "prediction_json", "after_json"):
                item[key.removesuffix("_json")] = json.loads(item.pop(key))
            result.append(item)
        return list(reversed(result))

    def submit_finding(self, generation_id, claim, conditions, evidence_ids, *, verified=False, commit=True):
        if not isinstance(claim, str) or not 1 <= len(claim) <= 2000:
            raise ValueError("Claim must contain 1..2000 characters")
        if not isinstance(conditions, str) or len(conditions) > 4000:
            raise ValueError("Conditions must be text up to 4000 characters")
        if not isinstance(evidence_ids, list) or not 1 <= len(evidence_ids) <= 100:
            raise ValueError("Findings need 1..100 existing experiment IDs")
        for item in evidence_ids:
            row = self.db.execute("SELECT status FROM experiments WHERE id=?", (item,)).fetchone()
            if not row or row["status"] != "completed":
                raise ValueError("Evidence must reference broker-recorded completed experiments")
        finding_id = uuid.uuid4().hex
        existing = self.db.execute("SELECT id FROM findings WHERE claim=? AND conditions=? AND status=?", (
            claim, conditions, "verified_prediction_test" if verified else "hypothesis")).fetchone()
        if existing:
            return existing["id"]
        self.db.execute("INSERT INTO findings VALUES(?,?,?,?,?,?,?)", (
            finding_id, generation_id, time.time(), claim,
            "verified_prediction_test" if verified else "hypothesis", conditions, canonical(evidence_ids)))
        if commit:
            self.db.commit()
        return finding_id

    def add_evaluation(self, generation_id, model, results, *, commit=True):
        evaluation_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO evaluations VALUES(?,?,?,?,?)", (
            evaluation_id, generation_id, time.time(), canonical(model), canonical(results)))
        if commit:
            self.db.commit()
        return evaluation_id

    def record_artifact(self, generation_id, path, sha256, size):
        existing = self.db.execute("SELECT id,sha256,bytes FROM artifacts WHERE generation_id=? AND path=?", (generation_id, str(path))).fetchone()
        if existing:
            if existing["sha256"] != sha256 or existing["bytes"] != size:
                raise ValueError("Previously archived artifact changed")
            return existing["id"]
        artifact_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?)", (artifact_id, generation_id, time.time(), str(path), sha256, size))
        self.db.commit()
        return artifact_id

    def artifacts(self, limit=30, *, before=None):
        query = "SELECT id,generation_id,path,sha256,bytes FROM artifacts"
        args = []
        if before is not None:
            row = self.db.execute("SELECT created,id FROM artifacts WHERE id=?", (before,)).fetchone()
            if row is None:
                raise ValueError("Unknown artifact pagination cursor")
            query += " WHERE (created,id)<(?,?)"
            args.extend((row["created"], row["id"]))
        query += " ORDER BY created DESC,id DESC LIMIT ?"
        args.append(max(1, min(100, int(limit))))
        return [dict(x) for x in self.db.execute(query, args)]

    def summary(self):
        result = {}
        for table in ("runs", "generations", "experiments", "findings", "evaluations", "artifacts"):
            result[table] = self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        result["recent_findings"] = [dict(x) for x in self.db.execute("SELECT id,claim,status,conditions FROM findings ORDER BY created DESC LIMIT 10")]
        result["recent_runs"] = [dict(x) for x in self.db.execute("SELECT id,mode,status,started,ended FROM runs ORDER BY started DESC LIMIT 5")]
        row = self.db.execute("SELECT AVG(error),MIN(error),MAX(error) FROM experiments WHERE status='completed'").fetchone()
        result["prediction_error"] = {"mean": row[0], "min": row[1], "max": row[2]}
        return result

    def recover_interrupted(self):
        """A restart cannot silently turn incomplete trials into successful evidence."""
        now = time.time()
        self.db.execute("UPDATE experiments SET status='interrupted',ended=?,reason='supervisor restart' WHERE status='pending'", (now,))
        self.db.execute("UPDATE generations SET status='interrupted',ended=? WHERE status='running'", (now,))
        self.db.execute("UPDATE runs SET status='interrupted',ended=? WHERE status='running'", (now,))
        self.db.commit()

    def export_digest(self):
        summary = self.summary()
        lines = ["# Robot playground findings", "", f"Experiments: {summary['experiments']}. Agent generations: {summary['generations']}.", "",
                 "Simulation evidence describes the simulator. Physical conclusions require physical trials.", ""]
        for finding in reversed(summary["recent_findings"]):
            lines.extend([f"- {finding['claim']}", f"  Status: {finding['status']}. Conditions: {finding['conditions']}"])
        lines += ["", "The SQLite archive preserves predictions committed before actions and their measured outcomes.", ""]
        target = self.directory / "digest.md"
        target.write_text("\n".join(lines), encoding="utf-8")
        return target
