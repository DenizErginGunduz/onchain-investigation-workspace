"""Local persistence behind a small interface; no provider-owned case identifiers."""
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import uuid
from app.domain import investigate
from app.bundles import import_bundle, replay_evidence
from app.tracing import replay_trace

class CaseStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, snapshot TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), created_at TEXT NOT NULL, disposition TEXT NOT NULL, text TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_notes_case ON notes(case_id, id);
                PRAGMA user_version = 1;
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, title, snapshot):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
            raise ValueError("Case title must contain 1–160 characters.")
        analysis = investigate(snapshot)
        if 'receipt_evidence' in analysis['snapshot']:
            review = replay_evidence(analysis['snapshot']['receipt_evidence'], analysis['snapshot'])
            if 'trace_evidence' in analysis['snapshot']:
                replay_trace(analysis['snapshot']['trace_evidence'], analysis['snapshot'], review)
        elif 'trace_evidence' in analysis['snapshot']:
            raise ValueError('Trace requires receipt evidence')
        case_id = "case_" + uuid.uuid4().hex[:16]
        with self.connect() as db:
            db.execute("INSERT INTO cases VALUES(?,?,?,?)", (case_id, title.strip(), datetime.now(timezone.utc).isoformat(), json.dumps(analysis["snapshot"])))
        return self.get(case_id)

    def import_case(self, payload):
        title, snapshot, _ = import_bundle(payload)
        return self.create(title, snapshot)

    def get(self, case_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if row is None:
                raise KeyError("Case not found.")
            notes = [dict(n) for n in db.execute("SELECT created_at,disposition,text FROM notes WHERE case_id=? ORDER BY id", (case_id,))]
        analysis = investigate(json.loads(row['snapshot']))
        snapshot = analysis['snapshot']
        review = replay_evidence(snapshot['receipt_evidence'], snapshot) if 'receipt_evidence' in snapshot else None
        trace = replay_trace(snapshot['trace_evidence'], snapshot, review) if 'trace_evidence' in snapshot else None
        return {"id": row["id"], "title": row["title"], "created_at": row["created_at"], "analysis": analysis, "notes": notes, 'receipt_review': review, 'trace_review': trace}

    def list(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,title,created_at FROM cases ORDER BY created_at DESC LIMIT 100")]

    def note(self, case_id, text, disposition):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 5000:
            raise ValueError("Write a note of 1–5,000 characters.")
        if disposition not in {"Under review", "More information needed", "Closed — no escalation"}:
            raise ValueError("Unknown disposition.")
        self.get(case_id)
        with self.connect() as db:
            db.execute("INSERT INTO notes(case_id,created_at,disposition,text) VALUES(?,?,?,?)", (case_id, datetime.now(timezone.utc).isoformat(), disposition, text.strip()))
        return self.get(case_id)
