"""
agent_lens.store
~~~~~~~~~~~~~~~~
SQLite persistence layer for agent-lens.
Uses aiosqlite for async I/O with a threading.Lock for write serialization.
Default database path: ~/.agent-lens/runs.db
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from agent_lens._textutil import flatten_run_text
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
    notes           TEXT,
    expected_output TEXT,
    metadata        TEXT DEFAULT '{}'
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

# Full-text index over run records. Kept in a separate statement because FTS5
# is not compiled into every SQLite build; a missing module must degrade the
# store to a LIKE-based fallback rather than crash initialization.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS run_search USING fts5(
    run_id UNINDEXED,
    name,
    notes,
    expected_output,
    body,
    tokenize='unicode61'
);
"""

# Terminal run states worth indexing — a still-running run has an incomplete
# body, so it is (re)indexed once it reaches one of these.
_TERMINAL_STATUSES = {"completed", "error", "forked"}


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
        self._fts_enabled = False
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
            # Migrate existing databases that predate new columns
            cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
            if "notes" not in cols:
                conn.execute("ALTER TABLE runs ADD COLUMN notes TEXT")
            if "expected_output" not in cols:
                conn.execute("ALTER TABLE runs ADD COLUMN expected_output TEXT")
            self._fts_enabled = self._try_init_fts(conn)
            conn.commit()

    def _try_init_fts(self, conn: sqlite3.Connection) -> bool:
        """Create the FTS5 search table, returning False if FTS5 is unavailable."""
        try:
            conn.executescript(_FTS_SCHEMA)
            return True
        except sqlite3.OperationalError:
            # SQLite build without the FTS5 module — search falls back to LIKE.
            return False

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
                    (id, name, start_time, end_time, status, parent_run_id, fork_span_id, notes, expected_output, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.name,
                    run.start_time,
                    run.end_time,
                    run.status if isinstance(run.status, str) else run.status.value,
                    run.parent_run_id,
                    run.fork_span_id,
                    run.notes,
                    run.expected_output,
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

        status_value = status.value if hasattr(status, "value") else status
        if status_value in _TERMINAL_STATUSES:
            self.reindex_run(run_id)

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
    # Search
    # ------------------------------------------------------------------

    def reindex_run(self, run_id: str) -> None:
        """Refresh the search-index entry for a single run. No-op without FTS5."""
        if not self._fts_enabled:
            return
        run = self.get_run(run_id)
        if run is None:
            return
        body = flatten_run_text(run, self.get_events(run_id))
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM run_search WHERE run_id = ?", (run_id,))
            conn.execute(
                "INSERT INTO run_search (run_id, name, notes, expected_output, body) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, run.name or "", run.notes or "", run.expected_output or "", body),
            )
            conn.commit()

    def reindex_all(self) -> None:
        """Rebuild the entire search index from the runs table. No-op without FTS5."""
        if not self._fts_enabled:
            return
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM run_search")
            conn.commit()
        for run in self.get_runs(limit=1_000_000):
            self.reindex_run(run.id)

    def search_runs(
        self,
        query: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Full-text search over run records.

        Returns ranked hits as dicts with run_id, name, status, score (bm25;
        None in fallback mode) and a highlighted snippet. Uses SQLite FTS5 when
        available and degrades to a substring scan otherwise.
        """
        query = (query or "").strip()
        if not query:
            return []
        if self._fts_enabled:
            return self._search_fts(query, status, limit, offset)
        return self._search_like(query, status, limit, offset)

    def _search_fts(
        self, query: str, status: str | None, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        conn = self._get_conn()
        # Build the index lazily the first time it is queried so runs that were
        # persisted directly (e.g. imports, tests) become searchable on demand.
        indexed = conn.execute("SELECT COUNT(*) AS c FROM run_search").fetchone()["c"]
        total_runs = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
        if indexed == 0 and total_runs > 0:
            self.reindex_all()

        params: list[Any] = [_to_match_query(query)]
        sql = (
            "SELECT s.run_id AS run_id, r.name AS name, r.status AS status, "
            "bm25(run_search) AS score, "
            "snippet(run_search, 4, '[', ']', ' … ', 12) AS snippet "
            "FROM run_search s JOIN runs r ON r.id = s.run_id "
            "WHERE run_search MATCH ?"
        )
        if status:
            sql += " AND r.status = ?"
            params.append(status)
        sql += " ORDER BY score LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "name": row["name"],
                "status": row["status"],
                "score": row["score"],
                "snippet": row["snippet"],
            }
            for row in rows
        ]

    def _search_like(
        self, query: str, status: str | None, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        needle = query.lower()
        matched: list[dict[str, Any]] = []
        page = 500
        run_offset = 0
        while True:
            runs = self.get_runs(limit=page, offset=run_offset)
            if not runs:
                break
            for run in runs:
                run_status = getattr(run.status, "value", run.status)
                if status and run_status != status:
                    continue
                body = flatten_run_text(run, self.get_events(run.id))
                if needle in body.lower():
                    matched.append(
                        {
                            "run_id": run.id,
                            "name": run.name,
                            "status": run_status,
                            "score": None,
                            "snippet": _make_snippet(body, needle),
                        }
                    )
            run_offset += page
        return matched[offset : offset + limit]

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
            notes=row.get("notes"),
            expected_output=row.get("expected_output"),
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


def _to_match_query(query: str) -> str:
    """Turn free-form user input into a safe FTS5 MATCH expression.

    Each whitespace-separated token is quoted as a phrase so characters that
    are otherwise FTS5 operators (``:``, ``-``, ``*``, ``"``) can't produce a
    syntax error. Multiple tokens are ANDed together.
    """
    tokens = query.split()
    safe = [f'"{tok.replace(chr(34), chr(34) * 2)}"' for tok in tokens]
    return " ".join(safe) if safe else '""'


def _make_snippet(body: str, needle: str, width: int = 60) -> str:
    """Return a short window of ``body`` around the first match of ``needle``."""
    idx = body.lower().find(needle)
    if idx < 0:
        return body[:width]
    start = max(0, idx - width // 2)
    end = min(len(body), idx + len(needle) + width // 2)
    prefix = "… " if start > 0 else ""
    suffix = " …" if end < len(body) else ""
    return f"{prefix}{body[start:end]}{suffix}"


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
