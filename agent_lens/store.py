"""
agent_lens.store
~~~~~~~~~~~~~~~~
SQLite persistence layer for agent-lens.
Uses aiosqlite for async I/O with a threading.Lock for write serialization.
Default database path: ~/.agent-lens/runs.db
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from agent_lens.models import Event, EventType, Run, RunStatus, Span


DEFAULT_DB_PATH = Path.home() / ".agent-lens" / "runs.db"

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    start_time    REAL NOT NULL,
    end_time      REAL,
    status        TEXT NOT NULL DEFAULT 'running',
    parent_run_id TEXT,
    fork_span_id  TEXT,
    metadata      TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_runs_start_time ON runs(start_time DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status     ON runs(status);

CREATE TABLE IF NOT EXISTS spans (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id),
    parent_id   TEXT,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    start_time  REAL NOT NULL,
    end_time    REAL,
    status      TEXT NOT NULL DEFAULT 'ok',
    metadata    TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_spans_run_id     ON spans(run_id);
CREATE INDEX IF NOT EXISTS idx_spans_parent_id  ON spans(parent_id);
CREATE INDEX IF NOT EXISTS idx_spans_start_time ON spans(start_time);

CREATE TABLE IF NOT EXISTS events (
    id             TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES runs(id),
    span_id        TEXT NOT NULL REFERENCES spans(id),
    parent_span_id TEXT,
    type           TEXT NOT NULL,
    timestamp      REAL NOT NULL,
    data           TEXT DEFAULT '{}',
    metadata       TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_run_id    ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_span_id   ON events(span_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_type      ON events(type);
"""


class Store:
    """
    SQLite-backed storage for runs, spans, and events.

    Thread-safe: uses a single write lock to serialize SQLite writes.
    Reads are concurrent (SQLite WAL mode).

    Usage:
        store = Store()  # uses default path
        store = Store(path="/tmp/test.db")  # custom path
        store.save_run(run)
        runs = store.get_runs()
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else DEFAULT_DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Return a thread-local SQLite connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.executescript(_SCHEMA)
            conn.commit()

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def save_run(self, run: Run) -> None:
        """Insert or replace a run record."""
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT OR REPLACE INTO runs
                    (id, name, start_time, end_time, status, parent_run_id, fork_span_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.name,
                    run.start_time,
                    run.end_time,
                    run.status if isinstance(run.status, str) else run.status.value,
                    run.parent_run_id,
                    run.fork_span_id,
                    json.dumps(run.metadata),
                ),
            )
            conn.commit()

    def get_run(self, run_id: str) -> Run | None:
        """Retrieve a single run by ID."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_run(dict(row))

    def get_runs(self, limit: int = 100, offset: int = 0) -> list[Run]:
        """Return runs ordered by start_time descending."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY start_time DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._row_to_run(dict(r)) for r in rows]

    def update_run_status(self, run_id: str, status: RunStatus, end_time: float | None = None) -> None:
        """Update run status and optionally set end_time."""
        with self._write_lock:
            conn = self._get_conn()
            if end_time is not None:
                conn.execute(
                    "UPDATE runs SET status = ?, end_time = ? WHERE id = ?",
                    (status.value if hasattr(status, "value") else status, end_time, run_id),
                )
            else:
                conn.execute(
                    "UPDATE runs SET status = ? WHERE id = ?",
                    (status.value if hasattr(status, "value") else status, run_id),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # Spans
    # ------------------------------------------------------------------

    def save_span(self, span: Span) -> None:
        """Insert or replace a span record."""
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT OR REPLACE INTO spans
                    (id, run_id, parent_id, name, type, start_time, end_time, status, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    span.id,
                    span.run_id,
                    span.parent_id,
                    span.name,
                    span.type,
                    span.start_time,
                    span.end_time,
                    span.status,
                    json.dumps(span.metadata if hasattr(span, "metadata") else {}),
                ),
            )
            conn.commit()

    def get_spans(self, run_id: str, include_parent_spans: bool = False) -> list[Span]:
        """
        Return all spans for a run.
        If include_parent_spans is True and the run is a fork, also return spans
        from the parent run up to fork_span_id.
        """
        conn = self._get_conn()
        spans = []

        if include_parent_spans:
            run = self.get_run(run_id)
            if run and run.parent_run_id and run.fork_span_id:
                # Get parent spans up to and including the fork point
                parent_spans_rows = conn.execute(
                    """
                    SELECT * FROM spans
                    WHERE run_id = ? AND start_time <= (
                        SELECT start_time FROM spans WHERE id = ?
                    )
                    ORDER BY start_time ASC
                    """,
                    (run.parent_run_id, run.fork_span_id),
                ).fetchall()
                spans.extend(self._row_to_span(dict(r)) for r in parent_spans_rows)

        own_rows = conn.execute(
            "SELECT * FROM spans WHERE run_id = ? ORDER BY start_time ASC",
            (run_id,),
        ).fetchall()
        spans.extend(self._row_to_span(dict(r)) for r in own_rows)
        return spans

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def save_event(self, event: Event) -> None:
        """Insert an event record."""
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT OR REPLACE INTO events
                    (id, run_id, span_id, parent_span_id, type, timestamp, data, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.run_id,
                    event.span_id,
                    event.parent_span_id,
                    event.type if isinstance(event.type, str) else event.type.value,
                    event.timestamp,
                    json.dumps(event.data),
                    json.dumps(event.metadata),
                ),
            )
            conn.commit()

    def get_events(self, run_id: str, span_id: str | None = None) -> list[Event]:
        """Return events for a run, optionally filtered by span."""
        conn = self._get_conn()
        if span_id:
            rows = conn.execute(
                "SELECT * FROM events WHERE run_id = ? AND span_id = ? ORDER BY timestamp ASC",
                (run_id, span_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY timestamp ASC",
                (run_id,),
            ).fetchall()
        return [self._row_to_event(dict(r)) for r in rows]

    def get_event_count(self) -> int:
        """Return total event count (useful for tests)."""
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) as cnt FROM events").fetchone()
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_run(row: dict[str, Any]) -> Run:
        return Run(
            id=row["id"],
            name=row["name"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            status=RunStatus(row["status"]),
            metadata=json.loads(row["metadata"] or "{}"),
            parent_run_id=row.get("parent_run_id"),
            fork_span_id=row.get("fork_span_id"),
        )

    @staticmethod
    def _row_to_span(row: dict[str, Any]) -> Span:
        return Span(
            id=row["id"],
            run_id=row["run_id"],
            parent_id=row.get("parent_id"),
            name=row["name"],
            type=row["type"],
            start_time=row["start_time"],
            end_time=row.get("end_time"),
            status=row.get("status", "ok"),
        )

    @staticmethod
    def _row_to_event(row: dict[str, Any]) -> Event:
        return Event(
            id=row["id"],
            run_id=row["run_id"],
            span_id=row["span_id"],
            parent_span_id=row.get("parent_span_id"),
            type=EventType(row["type"]),
            timestamp=row["timestamp"],
            data=json.loads(row["data"] or "{}"),
            metadata=json.loads(row["metadata"] or "{}"),
        )

    def close(self) -> None:
        """Close the thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


# Module-level singleton store (lazy-initialized)
_default_store: Store | None = None
_store_lock = threading.Lock()


def get_default_store(path: str | Path | None = None) -> Store:
    """Return (or create) the module-level default Store singleton."""
    global _default_store
    if _default_store is None:
        with _store_lock:
            if _default_store is None:
                _default_store = Store(path=path)
    return _default_store


def reset_default_store(path: str | Path | None = None) -> Store:
    """Reset the default store (useful in tests)."""
    global _default_store
    with _store_lock:
        if _default_store is not None:
            _default_store.close()
        _default_store = Store(path=path)
    return _default_store
