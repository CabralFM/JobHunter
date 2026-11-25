"""Banco SQLite local: tudo que o pipeline faz fica registrado aqui.

Por que SQLite e não Postgres/Supabase? Roda num único computador, um arquivo só
(data/jobhunter.db), zero instalação, fácil de copiar/backup, e abre no DB Browser for
SQLite ou no Datasette. Se um dia quiser um painel web compartilhado, migra-se depois.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY,
    dedup_key     TEXT UNIQUE,
    source        TEXT, external_id TEXT, url TEXT, apply_url TEXT, apply_email TEXT,
    title TEXT, company TEXT, location TEXT, country TEXT, workload TEXT,
    language TEXT, seniority TEXT, hard_requirements TEXT,
    description   TEXT, posted_at TEXT, scraped_at TEXT,
    also_seen_on  TEXT DEFAULT '[]',      -- outras fontes onde a mesma vaga apareceu
    raw           TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    candidate TEXT,
    triage_relevant INTEGER, triage_reason TEXT,
    score_current REAL, score_potential REAL, desire_fit REAL,
    priority REAL,                -- nota final usada para ordenar
    decision TEXT,                -- apply | review | skip | knockout
    assessment_json TEXT,         -- MatchAssessment completo
    model TEXT, created_at TEXT,
    feedback TEXT,                -- você marca: good | bad (para calibrar limiares)
    UNIQUE(job_id, candidate)
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    candidate TEXT,
    status TEXT,                  -- drafted | approved | sent | submitted_manually | interview | rejected | offer
    method TEXT,                  -- email | portal | linkedin
    cv_markdown TEXT, cover_letter_markdown TEXT,
    cv_path TEXT, letter_path TEXT,
    changes_made TEXT, facts_used TEXT,
    created_at TEXT, updated_at TEXT, notes TEXT,
    UNIQUE(job_id, candidate)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, stats TEXT
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---------- jobs ----------
    def upsert_job(self, job: Job) -> tuple[int, bool]:
        """Insere a vaga; se já existir (mesma empresa+título), só registra a nova fonte.

        Retorna (job_id, is_new).
        """
        row = self.conn.execute("SELECT id, also_seen_on, description FROM jobs WHERE dedup_key=?",
                                (job.dedup_key,)).fetchone()
        if row:
            seen = set(json.loads(row["also_seen_on"]))
            seen.add(job.source)
            # se a nova fonte trouxe descrição mais completa, guarda a mais longa
            desc = job.description if len(job.description or "") > len(row["description"] or "") else row["description"]
            self.conn.execute("UPDATE jobs SET also_seen_on=?, description=?, apply_url=COALESCE(apply_url, ?),"
                              " apply_email=COALESCE(apply_email, ?) WHERE id=?",
                              (json.dumps(sorted(seen)), desc, job.apply_url, job.apply_email, row["id"]))
            self.conn.commit()
            return row["id"], False
        cur = self.conn.execute(
            """INSERT INTO jobs (dedup_key, source, external_id, url, apply_url, apply_email, title, company,
               location, country, workload, description, posted_at, scraped_at, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job.dedup_key, job.source, job.external_id, job.url, job.apply_url, job.apply_email, job.title,
             job.company, job.location, job.country, job.workload, job.description, job.posted_at,
             job.scraped_at, json.dumps(job.raw, default=str)[:20000]),
        )
        self.conn.commit()
        return cur.lastrowid, True

    def set_triage(self, job_id: int, language: str, seniority: str, hard_requirements: list[str]):
        self.conn.execute("UPDATE jobs SET language=?, seniority=?, hard_requirements=? WHERE id=?",
                          (language, seniority, json.dumps(hard_requirements, ensure_ascii=False), job_id))
        self.conn.commit()

    def job(self, job_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    # ---------- matches ----------
    def has_match(self, job_id: int, candidate: str) -> bool:
        return self.conn.execute("SELECT 1 FROM matches WHERE job_id=? AND candidate=?",
                                 (job_id, candidate)).fetchone() is not None

    def save_match(self, job_id: int, candidate: str, **fields):
        fields.setdefault("created_at", now())
        cols = ["job_id", "candidate", *fields]
        self.conn.execute(
            f"INSERT OR REPLACE INTO matches ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            (job_id, candidate, *fields.values()),
        )
        self.conn.commit()

    def matches_to_tailor(self, candidate: str, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT m.*, j.title, j.company FROM matches m JOIN jobs j ON j.id=m.job_id
               LEFT JOIN applications a ON a.job_id=m.job_id AND a.candidate=m.candidate
               WHERE m.candidate=? AND m.decision='apply' AND a.id IS NULL
               ORDER BY m.priority DESC LIMIT ?""", (candidate, limit)).fetchall()

    # ---------- applications ----------
    def save_application(self, job_id: int, candidate: str, **fields):
        fields.setdefault("created_at", now())
        fields["updated_at"] = now()
        cols = ["job_id", "candidate", *fields]
        self.conn.execute(
            f"INSERT OR REPLACE INTO applications ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            (job_id, candidate, *fields.values()),
        )
        self.conn.commit()

    def set_application_status(self, app_id: int, status: str, notes: str | None = None):
        self.conn.execute("UPDATE applications SET status=?, updated_at=?, notes=COALESCE(?, notes) WHERE id=?",
                          (status, now(), notes, app_id))
        self.conn.commit()

    # ---------- runs ----------
    def log_run(self, started_at: str, stats: dict):
        self.conn.execute("INSERT INTO runs (started_at, finished_at, stats) VALUES (?,?,?)",
                          (started_at, now(), json.dumps(stats)))
        self.conn.commit()
