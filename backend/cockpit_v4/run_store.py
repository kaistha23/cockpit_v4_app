"""
Durable runs, events, messages, artifacts, leases and idempotency.

Why this is SQLite with WAL rather than a dict
-----------------------------------------------
A run that exists only in a worker's memory disappears when the worker does,
and the user is left with a spinner that never resolves and a paid provider
call nobody can account for. Every fact that a later decision depends on --
the run's state and version, the event sequence, the exact submitted code,
the result artifacts, the usage reservations, the worker lease -- is committed
here before the next side effect happens.

WAL is on because the API process reads the same file the worker writes, and
a reader that blocks behind a writer turns a status poll into a timeout.

`STORAGE_UNAVAILABLE` is a real terminal outcome. If this store cannot commit,
the caller does NOT proceed to the next paid operation and does NOT claim the
work was accepted -- see `orchestration` and `routes`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from backend.cockpit_v4.events import Event
from backend.cockpit_v4.states import TERMINAL_STATES

SCHEMA_VERSION = 1


class StorageUnavailable(RuntimeError):
    """The durable store could not commit. Never swallowed, never assumed."""


class IdempotencyConflict(RuntimeError):
    """Same key, different body. A typed conflict, not a silent second run."""


class TerminalAlready(RuntimeError):
    """The run already settled. A late writer cannot overwrite the outcome."""


class LeaseLost(RuntimeError):
    """This worker no longer owns the run. Its writes are fenced off."""


_DDL = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  question TEXT NOT NULL,
  mode TEXT NOT NULL,
  release_id TEXT NOT NULL,
  ui_filters TEXT NOT NULL DEFAULT '{}',
  state TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 0,
  error_code TEXT NOT NULL DEFAULT '',
  error_id TEXT NOT NULL DEFAULT '',
  operation TEXT NOT NULL DEFAULT '',
  final_response TEXT NOT NULL DEFAULT '',
  budget TEXT NOT NULL DEFAULT '{}',
  startup_sha TEXT NOT NULL DEFAULT '',
  route TEXT NOT NULL DEFAULT 'cockpit_v4',
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  delivered_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deadline_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS runs_thread ON runs(thread_id, created_at);
CREATE INDEX IF NOT EXISTS runs_state ON runs(state);

CREATE TABLE IF NOT EXISTS events (
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  payload TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS messages (
  run_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  PRIMARY KEY (run_id, ordinal)
);

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  release_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT '{}',
  columns TEXT NOT NULL DEFAULT '[]',
  row_count INTEGER NOT NULL DEFAULT 0,
  body TEXT NOT NULL,
  code_digest TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS artifacts_run ON artifacts(run_id);

CREATE TABLE IF NOT EXISTS submissions (
  submission_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  round INTEGER NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL,
  no_progress_key TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS submissions_run ON submissions(run_id);

CREATE TABLE IF NOT EXISTS reservations (
  reservation_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  purpose TEXT NOT NULL,
  reserved_usd REAL NOT NULL,
  settled_usd REAL,
  uncertain INTEGER NOT NULL DEFAULT 0,
  usage TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reservations_run ON reservations(run_id);

CREATE TABLE IF NOT EXISTS idempotency (
  principal_id TEXT NOT NULL,
  key TEXT NOT NULL,
  body_digest TEXT NOT NULL,
  run_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (principal_id, key)
);

CREATE TABLE IF NOT EXISTS leases (
  run_id TEXT PRIMARY KEY,
  worker_id TEXT NOT NULL,
  fence INTEGER NOT NULL,
  heartbeat_at TEXT NOT NULL,
  claimed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
  run_id TEXT PRIMARY KEY,
  claimed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_preferences (
  tenant_id TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (tenant_id, principal_id)
);

CREATE TABLE IF NOT EXISTS threads (
  thread_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  -- Empty until the first question arrives or someone renames it. A thread
  -- is titled by what was ASKED in it, never by a model call made purely to
  -- name a conversation.
  title TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS turns (
  turn_id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_thread ON turns(thread_id, ordinal);

CREATE TABLE IF NOT EXISTS summaries (
  thread_id TEXT PRIMARY KEY,
  covered_through_ordinal INTEGER NOT NULL,
  body TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS thread_context (
  thread_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS details (
  detail_ref TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_analyses (
  saved_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  title TEXT NOT NULL,
  note TEXT NOT NULL,
  question TEXT NOT NULL,
  release_id TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS saved_by_tenant
  ON saved_analyses(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS investigations (
  investigation_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  status TEXT NOT NULL,
  origin TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS investigations_by_tenant
  ON investigations(tenant_id, updated_at);

CREATE TABLE IF NOT EXISTS investigation_items (
  entry_id TEXT PRIMARY KEY,
  investigation_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref_id TEXT NOT NULL,
  label TEXT NOT NULL,
  added_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS investigation_items_by_parent
  ON investigation_items(investigation_id, created_at);

CREATE TABLE IF NOT EXISTS comments (
  comment_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  subject_kind TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  author_id TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS comments_by_subject
  ON comments(tenant_id, subject_kind, subject_id, created_at);

CREATE TABLE IF NOT EXISTS shares (
  share_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  subject_kind TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  shared_by TEXT NOT NULL,
  audience_id TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS shares_by_subject
  ON shares(tenant_id, subject_kind, subject_id, created_at);
CREATE INDEX IF NOT EXISTS shares_by_audience
  ON shares(tenant_id, audience_id, created_at);

CREATE TABLE IF NOT EXISTS notifications (
  notification_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  recipient TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  subject_kind TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  state TEXT NOT NULL,
  transport TEXT NOT NULL,
  receipt TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notifications_by_tenant
  ON notifications(tenant_id, created_at);
"""


#: The book a row belongs to when it predates domains. Every such row IS
#: corporate: that is the only book the runtime had when it was written.
_DEFAULT_DOMAIN = "corporate"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class RunRecord:
    run_id: str
    thread_id: str
    tenant_id: str
    principal_id: str
    question: str
    mode: str
    release_id: str
    state: str
    version: int
    provider: str = ""
    model: str = ""
    #: Which BOOK this run was accepted in, and the exact bytes it was
    #: accepted against. Read by the worker rather than re-derived, so a run
    #: settled after the reader switched the page still reads the book it
    #: was asked in.
    domain_id: str = _DEFAULT_DOMAIN
    release_fingerprint: str = ""
    error_code: str = ""
    error_id: str = ""
    operation: str = ""
    final_response: dict[str, Any] | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    ui_filters: dict[str, Any] = field(default_factory=dict)
    startup_sha: str = ""
    route: str = "cockpit_v4"
    cancel_requested: bool = False
    delivered_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    deadline_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "thread_id": self.thread_id,
            "mode": self.mode, "release_id": self.release_id,
            "provider": self.provider, "model": self.model,
            "state": self.state, "version": self.version,
            "error_code": self.error_code, "error_id": self.error_id,
            "operation": self.operation, "final_response": self.final_response,
            "budget": self.budget, "route": self.route,
            "cancel_requested": self.cancel_requested,
            "delivered_at": self.delivered_at, "created_at": self.created_at,
            "updated_at": self.updated_at, "deadline_at": self.deadline_at}


class RunStore:
    """The durable store. One SQLite file, WAL, thread-safe connections."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._local = threading.local()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._shared: sqlite3.Connection | None = None
        if self.path == ":memory:":
            # One shared connection: a per-thread :memory: database would be a
            # different database per thread, which is not a store.
            self._shared = sqlite3.connect(
                ":memory:", check_same_thread=False, timeout=30.0)
            self._shared.row_factory = sqlite3.Row
            self._guard = threading.RLock()
        self._migrate()

    # -- connections ----------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """A usable connection, or `StorageUnavailable`.

        Wrapped deliberately: a corrupt file, a revoked mount or a full disk
        must reach the caller as the TYPED outcome the state machine declares,
        so the run stops with STORAGE_UNAVAILABLE and does not launch the next
        paid operation. A raw sqlite error here would surface as an
        unclassified internal failure and send an operator to the wrong place.
        """
        if self._shared is not None:
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            try:
                conn = sqlite3.connect(self.path, timeout=30.0,
                                       isolation_level=None)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=30000")
            except sqlite3.Error as exc:
                raise StorageUnavailable(
                    f"the V4 state database at {self.path} is not usable: "
                    f"{exc}") from exc
            self._local.conn = conn
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction, which ALWAYS ends.

        The rollback used to be reached only by `sqlite3.Error`. Any other
        exception raised inside the block -- a validation failure, an
        HTTPException, a KeyError in the caller's own code -- propagated
        straight through, and `BEGIN IMMEDIATE` stayed open on this thread's
        connection with the database's write lock held.

        Nothing ever released it. Every later writer waited out its thirty
        second `busy_timeout` and then failed with "database is locked",
        which reads as a storage problem and is not one: it is one earlier
        caller's exception, still holding the door.

        So the rollback is in `finally`, and it runs on the success path too
        -- a COMMIT that succeeded leaves no transaction to roll back, and
        one that did not is exactly the case this exists for.
        """
        conn = self._connect()
        lock = getattr(self, "_guard", None)
        if lock is not None:
            lock.acquire()
        started = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            started = True
            yield conn
            conn.execute("COMMIT")
            started = False
        except sqlite3.Error as exc:
            raise StorageUnavailable(str(exc)) from exc
        finally:
            if started:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    # A connection too broken to roll back is a connection
                    # this thread must not keep: the next caller would
                    # inherit its open transaction.
                    self._discard(conn)
            if lock is not None:
                lock.release()

    def _discard(self, conn: sqlite3.Connection) -> None:
        """Drop a connection this thread can no longer trust.

        Only ever the thread-local one: the shared in-memory connection IS
        the database, and closing it would delete the store.
        """
        if self._shared is not None:
            return
        if getattr(self._local, "conn", None) is conn:
            self._local.conn = None
        try:
            conn.close()
        except sqlite3.Error:
            pass

    #: Columns added after a table shipped. `CREATE TABLE IF NOT EXISTS` does
    #: not alter an existing table, so a database written by an earlier build
    #: keeps its old shape and every query naming the new column fails.
    _ADDED_COLUMNS = (
        ("threads", "title", "TEXT NOT NULL DEFAULT ''"),
        # A conversation belongs to ONE book, decided when it is created and
        # never afterwards. Stored here rather than resolved per request,
        # because a follow-up asked six turns later must reach the same
        # evidence as the question that opened the thread -- and a domain
        # re-derived from whatever the home page currently shows is not the
        # same thing as the domain this conversation was held in.
        ("threads", "domain_id", "TEXT NOT NULL DEFAULT 'corporate'"),
        ("threads", "release_id", "TEXT NOT NULL DEFAULT ''"),
        ("threads", "release_fingerprint", "TEXT NOT NULL DEFAULT ''"),
        ("runs", "domain_id", "TEXT NOT NULL DEFAULT 'corporate'"),
        ("runs", "release_fingerprint", "TEXT NOT NULL DEFAULT ''"),
        ("runs", "provider", "TEXT NOT NULL DEFAULT ''"),
        ("runs", "model", "TEXT NOT NULL DEFAULT ''"),
        ("artifacts", "domain_id", "TEXT NOT NULL DEFAULT 'corporate'"),
    )

    def _migrate(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_DDL)
            for table, column, declaration in self._ADDED_COLUMNS:
                existing = {row["name"] for row in
                            conn.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} "
                        f"{declaration}")
            if self._shared is not None:
                conn.commit()
        except sqlite3.Error as exc:
            raise StorageUnavailable(
                f"the V4 state database could not be prepared: {exc}") from exc

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- threads --------------------------------------------------------

    def create_thread(self, *, tenant_id: str, principal_id: str,
                      domain_id: str = "", release_id: str = "",
                      release_fingerprint: str = "") -> str:
        """A server-generated conversation id, pinned to one book.

        V3 shipped a hard-coded `cockpit-web` thread, which is one history
        shared by every user and every tab. This one also carries the domain
        it was opened in, which is what makes a follow-up reach the same
        evidence as the question before it.
        """
        thread_id = f"th-{uuid.uuid4().hex}"
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO threads(thread_id, tenant_id, principal_id, "
                "domain_id, release_id, release_fingerprint, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (thread_id, tenant_id, principal_id,
                 domain_id or _DEFAULT_DOMAIN, release_id,
                 release_fingerprint, _now()))
        return thread_id

    def thread_domain(self, thread_id: str) -> dict[str, str] | None:
        """The book this conversation is pinned to. None when it does not exist.

        There is no setter. A thread's domain is decided when it is created,
        because changing it would leave the turns already in the transcript
        pointing at evidence from a different book -- and nothing on the
        screen would say which turn came from where.
        """
        row = self._connect().execute(
            "SELECT domain_id, release_id, release_fingerprint "
            "FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
        if row is None:
            return None
        return {"domain_id": str(row["domain_id"] or _DEFAULT_DOMAIN),
                "release_id": str(row["release_id"] or ""),
                "release_fingerprint": str(row["release_fingerprint"] or "")}

    def run_for_key(self, principal_id: str, key: str
                    ) -> tuple[RunRecord | None, str]:
        """The run a previous request with this key created, if any.

        Intake consults this BEFORE the concurrency check: a retry of the
        same request is the same run, and refusing it as "already running"
        would turn a dropped acceptance response into an error the caller
        cannot resolve.
        """
        if not key:
            return None, ""
        conn = self._connect()
        row = conn.execute(
            "SELECT run_id, body_digest FROM idempotency "
            "WHERE principal_id=? AND key=?",
            (principal_id, key)).fetchone()
        if row is None:
            return None, ""
        return self._read_run(conn, row["run_id"]), str(row["body_digest"])

    def set_thread_context(self, thread_id: str, *, tenant_id: str,
                           kind: str, body: dict[str, Any]) -> None:
        """Attach a standing context to a conversation.

        Used by Investigate Further: the attention item the thread started
        from is carried by the THREAD, so every turn in it is read against
        that segment and quarter without the user retyping them. It is
        server-written and tenant-stamped; nothing in a request or a model
        response can set it.
        """
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO thread_context(thread_id, tenant_id, kind, "
                "body, created_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(thread_id) DO UPDATE SET kind=excluded.kind, "
                "body=excluded.body, created_at=excluded.created_at",
                (thread_id, tenant_id, kind,
                 json.dumps(body, ensure_ascii=False, default=str), _now()))

    def thread_context(self, thread_id: str, *, tenant_id: str = ""
                       ) -> dict[str, Any] | None:
        row = self._connect().execute(
            "SELECT tenant_id, kind, body, created_at FROM thread_context "
            "WHERE thread_id=?", (thread_id,)).fetchone()
        if row is None:
            return None
        if tenant_id and str(row["tenant_id"]) != tenant_id:
            return None
        try:
            body = json.loads(row["body"])
        except (TypeError, ValueError):
            return None
        return {"kind": str(row["kind"]), "created_at": str(row["created_at"]),
                "body": body}

    def thread_owner(self, thread_id: str) -> tuple[str, str] | None:
        row = self._connect().execute(
            "SELECT tenant_id, principal_id FROM threads WHERE thread_id=?",
            (thread_id,)).fetchone()
        return (row["tenant_id"], row["principal_id"]) if row else None

    def model_preference(self, *, tenant_id: str, principal_id: str) -> dict[str, str] | None:
        row = self._connect().execute("SELECT provider, model FROM model_preferences WHERE tenant_id=? AND principal_id=?", (tenant_id, principal_id)).fetchone()
        return {"provider": str(row["provider"]), "model": str(row["model"])} if row else None

    def set_model_preference(self, *, tenant_id: str, principal_id: str, provider: str, model: str) -> None:
        with self._tx() as conn:
            conn.execute("INSERT INTO model_preferences(tenant_id, principal_id, provider, model, updated_at) VALUES (?,?,?,?,?) ON CONFLICT(tenant_id, principal_id) DO UPDATE SET provider=excluded.provider, model=excluded.model, updated_at=excluded.updated_at", (tenant_id, principal_id, provider, model, _now()))

    # -- runs -----------------------------------------------------------

    def accept_run(self, *, thread_id: str, tenant_id: str,
                   principal_id: str, question: str, mode: str,
                   release_id: str, ui_filters: dict[str, Any],
                   provider: str = "", model: str = "",
                   idempotency_key: str, body_digest: str,
                   startup_sha: str, deadline_at: str,
                   domain_id: str = "",
                   release_fingerprint: str = ""
                   ) -> tuple[RunRecord, bool]:
        """Persist the run AND its outbox row in one transaction.

        Returning `(record, created)`. A lost HTTP acceptance response must
        not create a second paid run, so a repeated key with the same body
        returns the same run and `created=False`.
        """
        with self._tx() as conn:
            if idempotency_key:
                row = conn.execute(
                    "SELECT body_digest, run_id FROM idempotency "
                    "WHERE principal_id=? AND key=?",
                    (principal_id, idempotency_key)).fetchone()
                if row is not None:
                    if row["body_digest"] != body_digest:
                        raise IdempotencyConflict(
                            "This idempotency key was already used for a "
                            "different request.")
                    existing = self._read_run(conn, row["run_id"])
                    if existing is not None:
                        return existing, False

            run_id = f"run-{uuid.uuid4().hex}"
            now = _now()
            conn.execute(
                "INSERT INTO runs(run_id, thread_id, tenant_id, principal_id,"
                " question, mode, release_id, provider, model, domain_id,"
                " release_fingerprint, ui_filters, state, version,"
                " startup_sha, created_at, updated_at, deadline_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, thread_id, tenant_id, principal_id, question, mode,
                 release_id, provider, model, domain_id or _DEFAULT_DOMAIN,
                 release_fingerprint, json.dumps(ui_filters), "ACCEPTED", 0,
                 startup_sha, now, now, deadline_at))
            conn.execute(
                "INSERT INTO outbox(run_id, claimed, created_at) "
                "VALUES (?,0,?)", (run_id, now))
            if idempotency_key:
                conn.execute(
                    "INSERT INTO idempotency(principal_id, key, body_digest,"
                    " run_id, created_at) VALUES (?,?,?,?,?)",
                    (principal_id, idempotency_key, body_digest, run_id, now))
            record = self._read_run(conn, run_id)
        assert record is not None
        return record, True

    @staticmethod
    def _read_run(conn: sqlite3.Connection, run_id: str) -> RunRecord | None:
        row = conn.execute("SELECT * FROM runs WHERE run_id=?",
                           (run_id,)).fetchone()
        if row is None:
            return None
        return RunRecord(
            run_id=row["run_id"], thread_id=row["thread_id"],
            tenant_id=row["tenant_id"], principal_id=row["principal_id"],
            question=row["question"], mode=row["mode"],
            release_id=row["release_id"],
            provider=(row["provider"] if "provider" in row.keys() else "") or "",
            model=(row["model"] if "model" in row.keys() else "") or "",
            domain_id=(row["domain_id"] if "domain_id" in row.keys()
                       else _DEFAULT_DOMAIN) or _DEFAULT_DOMAIN,
            release_fingerprint=(row["release_fingerprint"]
                                 if "release_fingerprint" in row.keys()
                                 else "") or "",
            state=row["state"],
            version=int(row["version"]), error_code=row["error_code"],
            error_id=row["error_id"], operation=row["operation"],
            final_response=(json.loads(row["final_response"])
                            if row["final_response"] else None),
            budget=json.loads(row["budget"] or "{}"),
            ui_filters=json.loads(row["ui_filters"] or "{}"),
            startup_sha=row["startup_sha"], route=row["route"],
            cancel_requested=bool(row["cancel_requested"]),
            delivered_at=row["delivered_at"], created_at=row["created_at"],
            updated_at=row["updated_at"], deadline_at=row["deadline_at"])

    def get_run(self, run_id: str) -> RunRecord | None:
        return self._read_run(self._connect(), run_id)

    def update_state(self, run_id: str, *, expect_version: int, state: str,
                     operation: str = "", budget: dict[str, Any] | None = None,
                     error_code: str = "", error_id: str = "",
                     final_response: dict[str, Any] | None = None,
                     terminal: bool = False) -> RunRecord:
        """Compare-and-swap on `version`. A stale writer is refused.

        This is the fence. A worker whose lease expired, or a late provider
        callback, holds an old version and cannot overwrite a newer terminal
        state with its own.
        """
        with self._tx() as conn:
            row = conn.execute(
                "SELECT version, state FROM runs WHERE run_id=?",
                (run_id,)).fetchone()
            if row is None:
                raise StorageUnavailable(f"run {run_id} is not stored")
            if int(row["version"]) != int(expect_version):
                raise LeaseLost(
                    f"run {run_id} moved to version {row['version']} while "
                    f"this writer held {expect_version}")
            if row["state"] in TERMINAL_STATES and not terminal:
                raise TerminalAlready(
                    f"run {run_id} already settled as {row['state']}")
            if row["state"] in TERMINAL_STATES and terminal:
                raise TerminalAlready(
                    f"run {run_id} already settled as {row['state']}")
            conn.execute(
                "UPDATE runs SET state=?, version=version+1, operation=?,"
                " budget=COALESCE(?, budget), error_code=?, error_id=?,"
                " final_response=COALESCE(?, final_response), updated_at=?"
                " WHERE run_id=?",
                (state, operation,
                 json.dumps(budget) if budget is not None else None,
                 error_code, error_id,
                 json.dumps(final_response) if final_response is not None
                 else None, _now(), run_id))
            record = self._read_run(conn, run_id)
        assert record is not None
        return record

    def request_cancel(self, run_id: str) -> RunRecord | None:
        """Idempotent. Sets the flag; the terminal race is resolved by CAS."""
        with self._tx() as conn:
            conn.execute(
                "UPDATE runs SET cancel_requested=1, updated_at=? "
                "WHERE run_id=?", (_now(), run_id))
            return self._read_run(conn, run_id)

    def mark_delivered(self, run_id: str) -> None:
        """Presentation acknowledgement. Separate from the analytical status:
        an unacknowledged answer is still a completed answer."""
        with self._tx() as conn:
            conn.execute("UPDATE runs SET delivered_at=? WHERE run_id=?",
                         (_now(), run_id))

    # -- outbox and leases ----------------------------------------------

    def claim_next(self, worker_id: str) -> RunRecord | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT run_id FROM outbox WHERE claimed=0 "
                "ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return None
            run_id = row["run_id"]
            conn.execute("UPDATE outbox SET claimed=1 WHERE run_id=?",
                         (run_id,))
            conn.execute(
                "INSERT OR REPLACE INTO leases(run_id, worker_id, fence,"
                " heartbeat_at, claimed_at) VALUES (?,?,"
                " COALESCE((SELECT fence FROM leases WHERE run_id=?),0)+1,"
                " ?,?)", (run_id, worker_id, run_id, _now(), _now()))
            return self._read_run(conn, run_id)

    def heartbeat(self, run_id: str, worker_id: str) -> None:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE leases SET heartbeat_at=? WHERE run_id=? "
                "AND worker_id=?", (_now(), run_id, worker_id))
            if cur.rowcount == 0:
                raise LeaseLost(f"worker {worker_id} no longer holds {run_id}")

    def lease(self, run_id: str) -> dict[str, Any] | None:
        row = self._connect().execute(
            "SELECT * FROM leases WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def stale_runs(self, *, stale_seconds: float) -> list[RunRecord]:
        """Runs whose worker stopped heartbeating, for the supervisor."""
        cutoff = datetime.now(timezone.utc).timestamp() - stale_seconds
        out: list[RunRecord] = []
        conn = self._connect()
        for row in conn.execute(
                "SELECT r.run_id, l.heartbeat_at FROM runs r JOIN leases l "
                "ON l.run_id=r.run_id WHERE r.state NOT IN "
                f"({','.join('?' * len(TERMINAL_STATES))})",
                tuple(TERMINAL_STATES)).fetchall():
            try:
                beat = datetime.fromisoformat(row["heartbeat_at"]).timestamp()
            except ValueError:
                continue
            if beat < cutoff:
                record = self._read_run(conn, row["run_id"])
                if record is not None:
                    out.append(record)
        return out

    def expiring_runs(self, *, grace_seconds: float = 0.0) -> list[RunRecord]:
        """Runs past their deadline by more than `grace_seconds`.

        THE GRACE IS WHAT MAKES THE WATCHDOG A WATCHDOG. Two components
        enforce this deadline: the worker's own ledger, which raises inside
        the loop and settles the run by PUBLISHING the rows it already
        computed, and this, which settles it from outside and cannot publish
        anything the worker knows. They used to race on the same instant --
        a 2-second settlement margin against a 2-second poll -- and the
        watchdog won routinely, turning a recoverable partial answer into a
        bare "This request ran out of time" while the rows sat in the
        artifact table.

        They are not equals and must not race. The worker settles the
        ordinary case; the watchdog exists for a worker that is blocked on a
        socket or gone. Holding it back by a margin larger than a clean
        settlement takes gives the worker the ordinary case, and costs a
        genuinely lost run only the few seconds before its browser stops
        spinning.
        """
        conn = self._connect()
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(seconds=max(0.0, grace_seconds))
                  ).isoformat(timespec="milliseconds")
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE deadline_at != '' AND "
            f"deadline_at < ? AND state NOT IN "
            f"({','.join('?' * len(TERMINAL_STATES))})",
            (cutoff, *TERMINAL_STATES)).fetchall()
        return [r for r in (self._read_run(conn, x["run_id"]) for x in rows)
                if r is not None]

    def active_runs_for(self, *, thread_id: str = "",
                        principal_id: str = "") -> int:
        conn = self._connect()
        clause = "state NOT IN (" + ",".join("?" * len(TERMINAL_STATES)) + ")"
        params: list[Any] = list(TERMINAL_STATES)
        if thread_id:
            clause += " AND thread_id=?"
            params.append(thread_id)
        if principal_id:
            clause += " AND principal_id=?"
            params.append(principal_id)
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM runs WHERE {clause}",
            tuple(params)).fetchone()
        return int(row["n"])

    # -- events ---------------------------------------------------------

    def append_event(self, event: Event) -> Event:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq),0) AS s FROM events WHERE run_id=?",
                (event.run_id,)).fetchone()
            event.seq = int(row["s"]) + 1
            conn.execute(
                "INSERT INTO events(run_id, seq, payload, occurred_at) "
                "VALUES (?,?,?,?)",
                (event.run_id, event.seq,
                 json.dumps(event.to_dict(), ensure_ascii=False),
                 event.occurred_at))
        return event

    def events_since(self, run_id: str, after_seq: int = 0,
                     limit: int = 500) -> list[Event]:
        rows = self._connect().execute(
            "SELECT payload FROM events WHERE run_id=? AND seq>? "
            "ORDER BY seq LIMIT ?", (run_id, after_seq, limit)).fetchall()
        out = []
        for row in rows:
            data = json.loads(row["payload"])
            out.append(Event(**{k: v for k, v in data.items()
                                if k in Event.__dataclass_fields__}))
        return out

    def last_seq(self, run_id: str) -> int:
        row = self._connect().execute(
            "SELECT COALESCE(MAX(seq),0) AS s FROM events WHERE run_id=?",
            (run_id,)).fetchone()
        return int(row["s"])

    def put_detail(self, run_id: str, body: dict[str, Any]) -> str:
        """Write one operator-only record, redacted on the way IN.

        The redaction lives HERE, at the write, and not at the callers. It
        used to live on the orchestrator's `_detail` helper, and two writers
        -- the allowance envelope and the question-normalization report,
        which stores 8 KB of the reader's own text -- reached the table
        without passing through it. A guarantee that a caller can decline to
        honour is not a guarantee, and it became a live one the moment a
        route existed that could read these bodies back.
        """
        body = redact(body)
        ref = f"dt-{uuid.uuid4().hex[:16]}"
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO details(detail_ref, run_id, body, created_at) "
                "VALUES (?,?,?,?)",
                (ref, run_id, json.dumps(body, ensure_ascii=False,
                                         default=str), _now()))
        return ref

    def get_detail(self, detail_ref: str) -> dict[str, Any] | None:
        row = self._connect().execute(
            "SELECT run_id, body FROM details WHERE detail_ref=?",
            (detail_ref,)).fetchone()
        if row is None:
            return None
        return {"run_id": row["run_id"], "body": json.loads(row["body"])}

    # -- conversation messages ------------------------------------------

    def save_messages(self, run_id: str, messages: list[dict[str, Any]]) -> None:
        """The canonical provider history, so a resumed or inspected run has
        no dangling tool_use without its tool_result."""
        with self._tx() as conn:
            conn.execute("DELETE FROM messages WHERE run_id=?", (run_id,))
            for i, message in enumerate(messages):
                conn.execute(
                    "INSERT INTO messages(run_id, ordinal, role, content) "
                    "VALUES (?,?,?,?)",
                    (run_id, i, str(message.get("role") or ""),
                     json.dumps(message.get("content"), ensure_ascii=False,
                                default=str)))

    def load_messages(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._connect().execute(
            "SELECT role, content FROM messages WHERE run_id=? "
            "ORDER BY ordinal", (run_id,)).fetchall()
        return [{"role": r["role"], "content": json.loads(r["content"])}
                for r in rows]

    # -- artifacts ------------------------------------------------------

    def put_artifact(self, *, run_id: str, tenant_id: str, kind: str,
                     release_id: str, scope: dict[str, Any],
                     columns: list[str], rows: list[dict[str, Any]],
                     code_digest: str = "") -> str:
        artifact_id = f"art-{uuid.uuid4().hex[:16]}"
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO artifacts(artifact_id, run_id, tenant_id, kind,"
                " release_id, scope, columns, row_count, body, code_digest,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (artifact_id, run_id, tenant_id, kind, release_id,
                 json.dumps(scope), json.dumps(columns), len(rows),
                 json.dumps(rows, ensure_ascii=False, default=str),
                 code_digest, _now()))
        return artifact_id

    def artifact_ids_for_run(self, run_id: str, *,
                             tenant_id: str) -> list[str]:
        """Every result this run stored, oldest first.

        The orchestrator tracks these in memory as it creates them, which
        serves it well and serves nobody else. The SUPERVISOR settles a run
        whose worker is blocked or gone and has no such memory, so without
        this it can only discard rows that were computed, stored and
        individually serveable -- which is exactly what a reader saw when a
        wall clock passed mid-generation.

        Tenant-checked like `get_artifact`, for the same reason.
        """
        rows = self._connect().execute(
            "SELECT artifact_id FROM artifacts WHERE run_id=? AND "
            "tenant_id=? ORDER BY created_at, artifact_id",
            (run_id, tenant_id)).fetchall()
        return [str(r["artifact_id"]) for r in rows]

    def get_artifact(self, artifact_id: str, *,
                     tenant_id: str) -> dict[str, Any] | None:
        """Tenant-checked. An unauthorized reference returns None and the
        caller replies 'not available to you' -- it never confirms that some
        other tenant's artifact exists."""
        row = self._connect().execute(
            "SELECT * FROM artifacts WHERE artifact_id=?",
            (artifact_id,)).fetchone()
        if row is None or row["tenant_id"] != tenant_id:
            return None
        return {"artifact_id": row["artifact_id"], "run_id": row["run_id"],
                "kind": row["kind"], "release_id": row["release_id"],
                "scope": json.loads(row["scope"]),
                "columns": json.loads(row["columns"]),
                "row_count": int(row["row_count"]),
                "rows": json.loads(row["body"]),
                "code_digest": row["code_digest"],
                "created_at": row["created_at"]}

    # -- submissions and reservations -----------------------------------

    def record_submission(self, *, run_id: str, ordinal: int, round: int,
                          payload: dict[str, Any], status: str,
                          no_progress_key: str) -> str:
        submission_id = f"sub-{uuid.uuid4().hex[:12]}"
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO submissions(submission_id, run_id, ordinal,"
                " round, payload, status, no_progress_key, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (submission_id, run_id, ordinal, round,
                 json.dumps(payload, ensure_ascii=False, default=str), status,
                 no_progress_key, _now()))
        return submission_id

    def extend_deadline(self, run_id: str, deadline_at: str) -> None:
        """Push this run's watchdog deadline out. Never pulls it in.

        The supervisor settles on `deadline_at`, so widening the ledger alone
        would leave an analysis killed at sixty seconds by a watchdog reading
        a stale value.
        """
        with self._tx() as conn:
            conn.execute(
                "UPDATE runs SET deadline_at=?, updated_at=? "
                "WHERE run_id=? AND (deadline_at='' OR deadline_at < ?)",
                (deadline_at, _now(), run_id, deadline_at))

    def set_submission_status(self, submission_id: str, status: str) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE submissions SET status=? "
                         "WHERE submission_id=?", (status, submission_id))

    def submissions_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Every batch of code this run submitted, in the order submitted.

        INCLUDING THE ONES THAT WERE REFUSED. A submission stopped at the
        binder never ran and produced no artifact, so nothing downstream
        has any trace of it -- and it is the single most informative thing
        in the record, because it is the control working. "CreditProbe
        would not run this query, and here is the query and the reason" is
        the sentence a model risk reviewer opens the file to find.

        Nothing read this table. It was written on every submission and
        queried only for `no_progress_key`, so the exact SQL of every
        attempt had been on file all along with no way to get it out.

        A FAILED submission is written twice -- once in full when it is
        sent, and again afterwards carrying only the id of the step that
        failed. The second row is a marker rather than a submission, so
        rows are folded on `ordinal` and the one that actually holds the
        steps wins. Its later status is kept, because that is the outcome.
        """
        rows = self._connect().execute(
            "SELECT submission_id, ordinal, round, payload, status,"
            " no_progress_key, created_at FROM submissions WHERE run_id=?"
            " ORDER BY ordinal, created_at", (run_id,)).fetchall()
        folded: dict[int, dict[str, Any]] = {}
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError):  # pragma: no cover - corrupt row
                payload = {}
            ordinal = int(row["ordinal"])
            record = {"submission_id": row["submission_id"],
                      "ordinal": ordinal, "round": int(row["round"]),
                      "payload": payload, "status": row["status"],
                      "no_progress_key": row["no_progress_key"],
                      "created_at": row["created_at"]}
            previous = folded.get(ordinal)
            if previous is None:
                folded[ordinal] = record
                continue
            if payload.get("steps"):
                # The full submission, arriving after a marker.
                folded[ordinal] = {**record, "status": previous["status"]}
            else:
                # A marker, arriving after the submission it marks.
                folded[ordinal] = {**previous, "status": row["status"],
                                   "failed_step":
                                       str(payload.get("failed") or "")}
        return [folded[key] for key in sorted(folded)]

    def details_for_run(self, run_id: str) -> dict[str, dict[str, Any]]:
        """Every operator record this run wrote, by reference.

        One query rather than one per event. A governance record reads
        most of the details a run produced, and fetching them singly is
        the same work spread over fifty round trips.
        """
        rows = self._connect().execute(
            "SELECT detail_ref, body FROM details WHERE run_id=?",
            (run_id,)).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                out[row["detail_ref"]] = json.loads(row["body"] or "{}")
            except (TypeError, ValueError):  # pragma: no cover - corrupt row
                continue
        return out

    def no_progress_keys(self, run_id: str) -> set[str]:
        rows = self._connect().execute(
            "SELECT no_progress_key FROM submissions WHERE run_id=? "
            "AND no_progress_key != ''", (run_id,)).fetchall()
        return {r["no_progress_key"] for r in rows}

    def reserve(self, *, run_id: str, purpose: str,
                reserved_usd: float) -> str:
        reservation_id = f"res-{uuid.uuid4().hex[:12]}"
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO reservations(reservation_id, run_id, purpose,"
                " reserved_usd, created_at) VALUES (?,?,?,?,?)",
                (reservation_id, run_id, purpose, float(reserved_usd), _now()))
        return reservation_id

    def settle(self, reservation_id: str, *, settled_usd: float | None,
               usage: dict[str, Any], uncertain: bool = False) -> None:
        """`settled_usd=None` with `uncertain=True` holds the reservation as
        PENDING. A cancelled or disconnected provider call may still have been
        billed, and booking it as zero understates real spend."""
        with self._tx() as conn:
            conn.execute(
                "UPDATE reservations SET settled_usd=?, usage=?, uncertain=? "
                "WHERE reservation_id=?",
                (settled_usd, json.dumps(usage), 1 if uncertain else 0,
                 reservation_id))

    def spend(self, run_id: str) -> dict[str, Any]:
        rows = self._connect().execute(
            "SELECT reserved_usd, settled_usd, uncertain FROM reservations "
            "WHERE run_id=?", (run_id,)).fetchall()
        committed = pending = 0.0
        uncertain = False
        for row in rows:
            if row["settled_usd"] is None:
                pending += float(row["reserved_usd"])
                uncertain = uncertain or bool(row["uncertain"])
            else:
                committed += float(row["settled_usd"])
                if row["uncertain"]:
                    pending += float(row["reserved_usd"])
                    uncertain = True
        return {"committed_usd": round(committed, 6),
                "pending_usd": round(pending, 6), "uncertain": uncertain}

    # -- thread turns and summaries -------------------------------------

    def append_turn(self, *, thread_id: str, run_id: str, question: str,
                    answer: dict[str, Any]) -> str:
        """Record this run's exchange in its thread. Idempotent per run.

        One run is one turn. Writing it twice would put the same exchange in
        the transcript twice, so a run that already has a turn keeps the one
        it has -- which is what makes it safe to write the turn BEFORE the
        run is marked terminal, where it belongs.
        """
        # The first question names the conversation. Free, and correct: a
        # thread is about what was asked in it.
        self.title_thread_from_question(thread_id, question)
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT turn_id FROM turns WHERE run_id=?",
                (run_id,)).fetchone()
            if existing is not None:
                return str(existing["turn_id"])
            turn_id = f"turn-{uuid.uuid4().hex[:12]}"
            row = conn.execute(
                "SELECT COALESCE(MAX(ordinal),0) AS n FROM turns "
                "WHERE thread_id=?", (thread_id,)).fetchone()
            conn.execute(
                "INSERT INTO turns(turn_id, thread_id, run_id, ordinal,"
                " question, answer, created_at) VALUES (?,?,?,?,?,?,?)",
                (turn_id, thread_id, run_id, int(row["n"]) + 1, question,
                 json.dumps(answer, ensure_ascii=False, default=str), _now()))
        return turn_id

    def recent_turns(self, thread_id: str, limit: int = 3
                     ) -> list[dict[str, Any]]:
        """The last few turns, for the ANALYST's context. Deliberately few.

        This is what the model reads. `thread_turns` is what a reader reads,
        and the two are different sizes on purpose: a twenty-turn
        conversation should render in full and must not be sent in full.
        """
        rows = self._connect().execute(
            "SELECT turn_id, run_id, ordinal, question, answer, created_at "
            "FROM turns WHERE thread_id=? ORDER BY ordinal DESC LIMIT ?",
            (thread_id, limit)).fetchall()
        return [self._turn(r) for r in reversed(rows)]

    def thread_turns(self, thread_id: str) -> list[dict[str, Any]]:
        """EVERY turn, oldest first. The transcript a reader scrolls.

        A conversation that renders only its last few exchanges is not a
        transcript; it is a window, and a reader who scrolls up to find what
        they asked half an hour ago finds nothing.
        """
        rows = self._connect().execute(
            "SELECT turn_id, run_id, ordinal, question, answer, created_at "
            "FROM turns WHERE thread_id=? ORDER BY ordinal ASC",
            (thread_id,)).fetchall()
        return [self._turn(r) for r in rows]

    @staticmethod
    def _turn(row: Any) -> dict[str, Any]:
        return {"turn_id": row["turn_id"], "run_id": row["run_id"],
                "ordinal": int(row["ordinal"]), "question": row["question"],
                "answer": json.loads(row["answer"]),
                "created_at": row["created_at"]}

    def thread_created_at(self, thread_id: str) -> str:
        row = self._connect().execute(
            "SELECT created_at FROM threads WHERE thread_id=?",
            (thread_id,)).fetchone()
        return str(row["created_at"]) if row else ""

    def thread_title(self, thread_id: str) -> str:
        row = self._connect().execute(
            "SELECT title FROM threads WHERE thread_id=?",
            (thread_id,)).fetchone()
        return str(row["title"]) if row else ""

    def set_thread_title(self, thread_id: str, *, tenant_id: str,
                         title: str) -> bool:
        """Rename. Tenant-checked, so a URL is not an authorization."""
        with self._tx() as conn:
            changed = conn.execute(
                "UPDATE threads SET title=? WHERE thread_id=? AND tenant_id=?",
                (title.strip()[:200], thread_id, tenant_id)).rowcount
        return bool(changed)

    def title_thread_from_question(self, thread_id: str, question: str
                                   ) -> None:
        """Name an unnamed thread after the question that opened it.

        No model call. A conversation is titled by what was asked in it, and
        spending a generation to rephrase that would be paying for a
        paraphrase of something already on the screen.
        """
        text = " ".join(str(question or "").split())
        if not text:
            return
        with self._tx() as conn:
            conn.execute(
                "UPDATE threads SET title=? "
                "WHERE thread_id=? AND (title IS NULL OR title='')",
                (text[:200], thread_id))

    def recent_threads(self, *, tenant_id: str, principal_id: str = "",
                       limit: int = 5) -> list[dict[str, Any]]:
        """Conversations this principal can reopen, most recent first.

        Real threads with at least one completed turn, plus the attention
        item a thread was seeded from when there was one. A thread with no
        turn yet is not something to "continue": nothing happened in it.
        """
        rows = self._connect().execute(
            "SELECT th.thread_id, th.created_at, th.domain_id, "
            "       th.release_id, "
            "       MAX(t.ordinal) AS turns, MAX(t.created_at) AS last_at "
            "FROM threads th JOIN turns t ON t.thread_id = th.thread_id "
            "WHERE th.tenant_id = ?"
            + (" AND th.principal_id = ?" if principal_id else "")
            + " GROUP BY th.thread_id, th.created_at "
              "ORDER BY last_at DESC LIMIT ?",
            ((tenant_id, principal_id, limit) if principal_id
             else (tenant_id, limit))).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            thread_id = str(row["thread_id"])
            turns = self.recent_turns(thread_id, 1)
            latest = turns[-1] if turns else {}
            context = self.thread_context(thread_id, tenant_id=tenant_id)
            out.append({
                "thread_id": thread_id,
                # §36: what a reader picks a conversation by. The title is
                # what was asked in it, so a list of threads reads as a list
                # of questions rather than a list of identifiers.
                "title": self.thread_title(thread_id)
                         or str(latest.get("question") or ""),
                "turns": int(row["turns"] or 0),
                "last_activity_at": str(row["last_at"] or row["created_at"]),
                "last_question": str(latest.get("question") or ""),
                "last_disposition": str(
                    (latest.get("answer") or {}).get("disposition") or ""),
                "origin": ((context or {}).get("kind") or "ask"),
                "attention_item": ((context or {}).get("body") or {}).get(
                    "headline", ""),
                "segment": ((context or {}).get("body") or {}).get(
                    "segment", ""),
                # §18, §51. WHICH BOOK this conversation is in, so reopening
                # it puts the reader back in that book. A row without it
                # sends them into whatever the switch happened to be showing,
                # and their next question is refused for a reason that has
                # nothing to do with what they typed.
                "domain_id": str(row["domain_id"] or _DEFAULT_DOMAIN),
                "release_id": str(row["release_id"] or ""),
            })
        return out

    def turn_count(self, thread_id: str) -> int:
        row = self._connect().execute(
            "SELECT COUNT(*) AS n FROM turns WHERE thread_id=?",
            (thread_id,)).fetchone()
        return int(row["n"])

    def get_turn(self, turn_id: str, *, tenant_id: str
                 ) -> dict[str, Any] | None:
        row = self._connect().execute(
            "SELECT t.*, th.tenant_id AS owner FROM turns t JOIN threads th "
            "ON th.thread_id=t.thread_id WHERE t.turn_id=?",
            (turn_id,)).fetchone()
        if row is None or row["owner"] != tenant_id:
            return None
        return {"turn_id": row["turn_id"], "ordinal": int(row["ordinal"]),
                "question": row["question"],
                "answer": json.loads(row["answer"]),
                "created_at": row["created_at"]}

    def put_summary(self, *, thread_id: str, covered_through: int,
                    body: dict[str, Any], schema_version: str) -> bool:
        """Compare-and-swap on coverage. An older job cannot overwrite a
        newer summary just because it finished later."""
        with self._tx() as conn:
            row = conn.execute(
                "SELECT covered_through_ordinal FROM summaries "
                "WHERE thread_id=?", (thread_id,)).fetchone()
            if row is not None and int(
                    row["covered_through_ordinal"]) >= covered_through:
                return False
            conn.execute(
                "INSERT OR REPLACE INTO summaries(thread_id,"
                " covered_through_ordinal, body, schema_version, updated_at)"
                " VALUES (?,?,?,?,?)",
                (thread_id, covered_through,
                 json.dumps(body, ensure_ascii=False, default=str),
                 schema_version, _now()))
        return True

    def get_summary(self, thread_id: str) -> dict[str, Any] | None:
        row = self._connect().execute(
            "SELECT * FROM summaries WHERE thread_id=?",
            (thread_id,)).fetchone()
        if row is None:
            return None
        return {"covered_through_ordinal": int(row["covered_through_ordinal"]),
                "body": json.loads(row["body"]),
                "schema_version": row["schema_version"],
                "updated_at": row["updated_at"]}

    # -- saved analyses, investigations, comments, shares, outbox -------
    #
    # Every read below is tenant-checked in SQL rather than after the fact,
    # so a wrong tenant sees an empty result and never learns that the row
    # exists. The same rule the artifact reader follows.

    def save_analysis(self, *, tenant_id: str, principal_id: str,
                      thread_id: str, run_id: str, title: str, note: str,
                      question: str, release_id: str,
                      body: dict[str, Any]) -> dict[str, Any]:
        saved_id = f"save-{uuid.uuid4().hex[:16]}"
        created = _now()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO saved_analyses(saved_id, tenant_id,"
                " principal_id, thread_id, run_id, title, note, question,"
                " release_id, body, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (saved_id, tenant_id, principal_id, thread_id, run_id, title,
                 note, question, release_id,
                 json.dumps(body, ensure_ascii=False, default=str), created))
        return {"saved_id": saved_id, "tenant_id": tenant_id,
                "principal_id": principal_id, "thread_id": thread_id,
                "run_id": run_id, "title": title, "note": note,
                "question": question, "release_id": release_id,
                "body": body, "created_at": created}

    def get_saved_analysis(self, saved_id: str, *, tenant_id: str
                           ) -> dict[str, Any] | None:
        row = self._connect().execute(
            "SELECT * FROM saved_analyses WHERE saved_id=? AND tenant_id=?",
            (saved_id, tenant_id)).fetchone()
        if row is None:
            return None
        return {"saved_id": row["saved_id"], "tenant_id": row["tenant_id"],
                "principal_id": row["principal_id"],
                "thread_id": row["thread_id"], "run_id": row["run_id"],
                "title": row["title"], "note": row["note"],
                "question": row["question"], "release_id": row["release_id"],
                "body": json.loads(row["body"]),
                "created_at": row["created_at"]}

    def list_saved_analyses(self, *, tenant_id: str, principal_id: str = "",
                            limit: int = 20) -> list[dict[str, Any]]:
        sql = ("SELECT saved_id, title, question, run_id, thread_id,"
               " principal_id, created_at FROM saved_analyses"
               " WHERE tenant_id=?")
        args: list[Any] = [tenant_id]
        if principal_id:
            sql += " AND principal_id=?"
            args.append(principal_id)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in self._connect().execute(sql, args).fetchall()]

    def create_investigation(self, *, tenant_id: str, principal_id: str,
                             title: str, summary: str, origin: str = "",
                             thread_id: str = "") -> dict[str, Any]:
        investigation_id = f"inv-{uuid.uuid4().hex[:16]}"
        created = _now()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO investigations(investigation_id, tenant_id,"
                " principal_id, title, summary, status, origin, thread_id,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (investigation_id, tenant_id, principal_id, title, summary,
                 "open", origin, thread_id, created, created))
        return {"investigation_id": investigation_id, "tenant_id": tenant_id,
                "principal_id": principal_id, "title": title,
                "summary": summary, "status": "open", "origin": origin,
                "thread_id": thread_id, "created_at": created,
                "updated_at": created, "items": []}

    def get_investigation(self, investigation_id: str, *, tenant_id: str
                          ) -> dict[str, Any] | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM investigations WHERE investigation_id=?"
            " AND tenant_id=?", (investigation_id, tenant_id)).fetchone()
        if row is None:
            return None
        items = conn.execute(
            "SELECT entry_id, kind, ref_id, label, added_by, created_at"
            " FROM investigation_items WHERE investigation_id=? AND tenant_id=?"
            " ORDER BY created_at ASC, rowid ASC",
            (investigation_id, tenant_id)).fetchall()
        body = dict(row)
        body["items"] = [dict(i) for i in items]
        return body

    def list_investigations(self, *, tenant_id: str, limit: int = 20
                            ) -> list[dict[str, Any]]:
        return [dict(r) for r in self._connect().execute(
            "SELECT investigation_id, title, status, principal_id, origin,"
            " updated_at FROM investigations WHERE tenant_id=?"
            " ORDER BY updated_at DESC, rowid DESC LIMIT ?",
            (tenant_id, int(limit))).fetchall()]

    def add_investigation_item(self, *, investigation_id: str, tenant_id: str,
                               kind: str, ref_id: str, label: str,
                               added_by: str) -> dict[str, Any] | None:
        entry_id = f"ent-{uuid.uuid4().hex[:12]}"
        created = _now()
        with self._tx() as conn:
            owned = conn.execute(
                "SELECT 1 FROM investigations WHERE investigation_id=?"
                " AND tenant_id=?", (investigation_id, tenant_id)).fetchone()
            if owned is None:
                return None
            conn.execute(
                "INSERT INTO investigation_items(entry_id, investigation_id,"
                " tenant_id, kind, ref_id, label, added_by, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (entry_id, investigation_id, tenant_id, kind, ref_id, label,
                 added_by, created))
            conn.execute(
                "UPDATE investigations SET updated_at=?"
                " WHERE investigation_id=?", (created, investigation_id))
        return {"entry_id": entry_id, "kind": kind, "ref_id": ref_id,
                "label": label, "added_by": added_by, "created_at": created}

    def set_investigation_status(self, investigation_id: str, *,
                                 tenant_id: str, status: str) -> bool:
        with self._tx() as conn:
            changed = conn.execute(
                "UPDATE investigations SET status=?, updated_at=?"
                " WHERE investigation_id=? AND tenant_id=?",
                (status, _now(), investigation_id, tenant_id)).rowcount
        return bool(changed)

    def add_comment(self, *, tenant_id: str, subject_kind: str,
                    subject_id: str, author_id: str, body: str
                    ) -> dict[str, Any]:
        comment_id = f"cmt-{uuid.uuid4().hex[:12]}"
        created = _now()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO comments(comment_id, tenant_id, subject_kind,"
                " subject_id, author_id, body, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (comment_id, tenant_id, subject_kind, subject_id, author_id,
                 body, created))
        return {"comment_id": comment_id, "subject_kind": subject_kind,
                "subject_id": subject_id, "author_id": author_id,
                "body": body, "created_at": created}

    def list_comments(self, *, tenant_id: str, subject_kind: str,
                      subject_id: str, limit: int = 50
                      ) -> list[dict[str, Any]]:
        return [dict(r) for r in self._connect().execute(
            "SELECT comment_id, subject_kind, subject_id, author_id, body,"
            " created_at FROM comments WHERE tenant_id=? AND subject_kind=?"
            " AND subject_id=? ORDER BY created_at ASC, rowid ASC LIMIT ?",
            (tenant_id, subject_kind, subject_id, int(limit))).fetchall()]

    def add_share(self, *, tenant_id: str, subject_kind: str,
                  subject_id: str, shared_by: str, audience_id: str,
                  message: str) -> dict[str, Any]:
        share_id = f"shr-{uuid.uuid4().hex[:12]}"
        created = _now()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO shares(share_id, tenant_id, subject_kind,"
                " subject_id, shared_by, audience_id, message, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (share_id, tenant_id, subject_kind, subject_id, shared_by,
                 audience_id, message, created))
        return {"share_id": share_id, "subject_kind": subject_kind,
                "subject_id": subject_id, "shared_by": shared_by,
                "audience_id": audience_id, "message": message,
                "created_at": created}

    def list_shares(self, *, tenant_id: str, subject_kind: str = "",
                    subject_id: str = "", audience_id: str = "",
                    limit: int = 50) -> list[dict[str, Any]]:
        sql = ("SELECT share_id, subject_kind, subject_id, shared_by,"
               " audience_id, message, created_at FROM shares"
               " WHERE tenant_id=?")
        args: list[Any] = [tenant_id]
        if subject_kind:
            sql += " AND subject_kind=?"
            args.append(subject_kind)
        if subject_id:
            sql += " AND subject_id=?"
            args.append(subject_id)
        if audience_id:
            sql += " AND audience_id=?"
            args.append(audience_id)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in self._connect().execute(sql, args).fetchall()]

    def put_notification(self, *, tenant_id: str, actor_id: str,
                         recipient: str, subject: str, body: str,
                         subject_kind: str, subject_id: str, state: str,
                         transport: str, receipt: str, reason: str
                         ) -> dict[str, Any]:
        """Record a notification. Writing the row is not sending it."""
        notification_id = f"ntf-{uuid.uuid4().hex[:12]}"
        created = _now()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO notifications(notification_id, tenant_id,"
                " actor_id, recipient, subject, body, subject_kind,"
                " subject_id, state, transport, receipt, reason, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (notification_id, tenant_id, actor_id, recipient, subject,
                 body, subject_kind, subject_id, state, transport, receipt,
                 reason, created))
        return {"notification_id": notification_id, "recipient": recipient,
                "subject": subject, "body": body,
                "subject_kind": subject_kind, "subject_id": subject_id,
                "state": state, "transport": transport, "receipt": receipt,
                "reason": reason, "delivered": state == "SENT",
                "created_at": created}

    def principals_of(self, *, tenant_id: str, limit: int = 200) -> list[str]:
        """Who has actually used this tenant.

        Read off the runs, because that is the only record this service
        keeps of a person. It is not an org chart and the route that
        serves it says so: somebody who has never opened CreditProbe is
        not here, and offering a directory that quietly omits people is
        worse than one that admits what it is.
        """
        rows = self._connect().execute(
            "SELECT DISTINCT principal_id FROM runs WHERE tenant_id=?"
            " AND principal_id != '' ORDER BY principal_id LIMIT ?",
            (tenant_id, int(limit))).fetchall()
        return [str(r["principal_id"]) for r in rows]

    def inbox(self, *, tenant_id: str, recipient: str,
              limit: int = 50) -> list[dict[str, Any]]:
        """The messages addressed to ONE reader.

        `list_notifications` is the tenant's whole outbox, which is what an
        operator wants and the opposite of what a person wants. It also
        withholds the body -- correctly, for an outbox view -- so even
        filtering it would have handed a reader a subject line and no
        message.

        The body is here, because this IS the message.
        """
        rows = [dict(r) for r in self._connect().execute(
            "SELECT notification_id, actor_id, recipient, subject, body,"
            " subject_kind, subject_id, state, transport, receipt, reason,"
            " created_at FROM notifications"
            " WHERE tenant_id=? AND recipient=? AND state IN ('SENT',"
            " 'RECORDED') ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (tenant_id, recipient.strip(), int(limit))).fetchall()]
        for row in rows:
            row["delivered"] = row["state"] == "SENT"
        return rows

    def list_notifications(self, *, tenant_id: str, state: str = "",
                           limit: int = 50) -> list[dict[str, Any]]:
        sql = ("SELECT notification_id, recipient, subject, subject_kind,"
               " subject_id, state, transport, receipt, reason, created_at"
               " FROM notifications WHERE tenant_id=?")
        args: list[Any] = [tenant_id]
        if state:
            sql += " AND state=?"
            args.append(state)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        args.append(int(limit))
        rows = [dict(r) for r in self._connect().execute(sql, args).fetchall()]
        for row in rows:
            row["delivered"] = row["state"] == "SENT"
        return rows




#: Key names whose value is never persisted, at any depth.
_SECRET_HINTS = ("api_key", "apikey", "authorization", "cookie", "token",
                 "secret", "password", "credential")


def redact(body: Any) -> Any:
    """Strip anything that looks like a secret before it is persisted.

    Operator diagnostics may name a model and a request id. They may never
    carry a key, a cookie, an authorization header or an environment dump --
    and this runs on the way IN, so a downloadable trace cannot leak one.
    """
    if isinstance(body, dict):
        out = {}
        for key, value in body.items():
            if any(hint in str(key).lower() for hint in _SECRET_HINTS):
                out[key] = "[redacted]"
            else:
                out[key] = redact(value)
        return out
    if isinstance(body, list):
        return [redact(v) for v in body]
    if isinstance(body, str) and len(body) > 20:
        lowered = body.lower()
        if lowered.startswith(("sk-", "bearer ")):
            return "[redacted]"
    return body


__all__ = ["IdempotencyConflict", "LeaseLost", "RunRecord", "RunStore",
           "SCHEMA_VERSION", "StorageUnavailable", "TerminalAlready",
           "redact"]
