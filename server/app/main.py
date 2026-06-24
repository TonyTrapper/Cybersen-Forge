from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, Cookie, FastAPI, Form, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

PROJECT_NAME = os.getenv("PROJECT_NAME", "Cybersen Forge")
TEAM_NAME = os.getenv("TEAM_NAME", "Cybersen")
OPERATOR_NAME = os.getenv("OPERATOR_NAME", "TonyTrapper")
TEAM_NONCE = os.getenv("TEAM_NONCE", "SET-YOUR-NONCE")
ENROLLMENT_TOKEN = os.getenv("AGENT_ENROLLMENT_TOKEN", "change-me-enrollment-token")
OPERATOR_USERNAME = os.getenv("OPERATOR_USERNAME", "admin")
OPERATOR_PASSWORD = os.getenv("OPERATOR_PASSWORD", "change-me-operator-password")
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-session-secret")
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "/data/cybersen-forge.db"))
AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", "8"))
CHECKIN_INTERVAL_SECONDS = max(1, int(os.getenv("CHECKIN_INTERVAL_SECONDS", "1")))
COMMAND_TIMEOUT_SECONDS = max(1, int(os.getenv("COMMAND_TIMEOUT_SECONDS", "15")))
MAX_OUTPUT_BYTES = max(4096, int(os.getenv("MAX_OUTPUT_BYTES", "65536")))
SESSION_TTL_HOURS = max(1, int(os.getenv("SESSION_TTL_HOURS", "12")))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title=PROJECT_NAME, version="0.1.0", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


class EnrollRequest(BaseModel):
    hostname: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    os: str = Field(min_length=1, max_length=64)
    arch: str = Field(min_length=1, max_length=64)
    agent_version: str = Field(min_length=1, max_length=64)
    process_id: int | None = Field(default=None, ge=0)
    executable: str | None = Field(default=None, max_length=1024)
    sandbox_available: bool = False
    sandbox_runtime: str | None = Field(default=None, max_length=32)


class EnrollResponse(BaseModel):
    agent_id: str
    agent_token: str
    checkin_interval: int
    command_timeout: int
    max_output_bytes: int


class TaskPayload(BaseModel):
    id: int
    command: str
    mode: Literal["host", "sandbox"] = "host"


class CheckinResponse(BaseModel):
    task: TaskPayload | None = None
    checkin_interval: int


class HeartbeatRequest(BaseModel):
    agent_version: str | None = Field(default=None, max_length=64)
    process_id: int | None = Field(default=None, ge=0)
    executable: str | None = Field(default=None, max_length=1024)
    sandbox_available: bool | None = None
    sandbox_runtime: str | None = Field(default=None, max_length=32)


class ResultRequest(BaseModel):
    stdout: str = ""
    stderr: str = ""
    exit_code: int
    duration_ms: int = Field(ge=0)
    timed_out: bool = False


class OperatorTaskRequest(BaseModel):
    command: str = Field(min_length=1, max_length=2048)
    mode: Literal["host", "sandbox"] = "host"


class RenameAgentRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_session_token(username: str) -> str:
    payload = {
        "sub": username,
        "exp": int((utcnow() + timedelta(hours=SESSION_TTL_HOURS)).timestamp()),
        "nonce": secrets.token_hex(8),
    }
    body = b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(SESSION_SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{b64url_encode(signature)}"


def verify_session_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    body, supplied_signature = token.rsplit(".", 1)
    expected = hmac.new(SESSION_SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    try:
        supplied = b64url_decode(supplied_signature)
        if not hmac.compare_digest(expected, supplied):
            return None
        payload = json.loads(b64url_decode(body))
        if int(payload.get("exp", 0)) < int(utcnow().timestamp()):
            return None
        if payload.get("sub") != OPERATOR_USERNAME:
            return None
        return str(payload["sub"])
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def require_operator(forge_session: str | None = Cookie(default=None)) -> str:
    username = verify_session_token(forge_session)
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return username


def db_connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def initialize_database() -> None:
    with closing(db_connect()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL,
                hostname TEXT NOT NULL,
                username TEXT NOT NULL,
                os TEXT NOT NULL,
                arch TEXT NOT NULL,
                agent_version TEXT NOT NULL,
                enrolled_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                command TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                dispatched_at TEXT,
                completed_at TEXT,
                stdout TEXT,
                stderr TEXT,
                exit_code INTEGER,
                duration_ms INTEGER,
                FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_agents_last_seen ON agents(last_seen DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_agent_created ON tasks(agent_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            """
        )
        ensure_column(conn, "agents", "display_name", "TEXT")
        ensure_column(conn, "agents", "remote_ip", "TEXT")
        ensure_column(conn, "agents", "process_id", "INTEGER")
        ensure_column(conn, "agents", "executable", "TEXT")
        ensure_column(conn, "agents", "connection_type", "TEXT NOT NULL DEFAULT 'direct'")
        ensure_column(conn, "agents", "parent_agent_id", "TEXT")
        ensure_column(conn, "agents", "sandbox_available", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "agents", "sandbox_runtime", "TEXT")
        ensure_column(conn, "tasks", "timed_out", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "tasks", "execution_mode", "TEXT NOT NULL DEFAULT 'host'")
        conn.commit()


@app.on_event("startup")
def startup() -> None:
    initialize_database()


def authenticate_agent(agent_id: str, authorization: str | None) -> sqlite3.Row:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing agent token")
    supplied_token = authorization.removeprefix("Bearer ").strip()
    with closing(db_connect()) as conn:
        agent = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if agent is None or not secrets.compare_digest(agent["token_hash"], token_hash(supplied_token)):
        raise HTTPException(status_code=401, detail="Invalid agent token")
    return agent


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def agent_status(last_seen: str) -> str:
    try:
        age = (utcnow() - parse_iso(last_seen)).total_seconds()
    except ValueError:
        return "unknown"
    if age <= AGENT_TIMEOUT_SECONDS:
        return "online"
    if age <= AGENT_TIMEOUT_SECONDS * 3:
        return "idle"
    return "offline"


def age_seconds(last_seen: str) -> int:
    try:
        return max(0, int((utcnow() - parse_iso(last_seen)).total_seconds()))
    except ValueError:
        return 0


def validate_operator_command(command: str, mode: str) -> str:
    stripped = command.strip()
    if not stripped:
        raise HTTPException(status_code=400, detail="Command cannot be empty")
    maximum = 2048 if mode == "sandbox" else 512
    if len(stripped) > maximum:
        raise HTTPException(status_code=400, detail="Command is too long")
    if "\x00" in stripped or "\r" in stripped or "\n" in stripped:
        raise HTTPException(status_code=400, detail="Command contains invalid control characters")
    return stripped if mode == "sandbox" else " ".join(stripped.split())


def serialize_task(task: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": task["id"],
        "agent_id": task["agent_id"],
        "command": task["command"],
        "mode": task["execution_mode"] or "host",
        "status": task["status"],
        "created_at": task["created_at"],
        "dispatched_at": task["dispatched_at"],
        "completed_at": task["completed_at"],
        "stdout": task["stdout"] or "",
        "stderr": task["stderr"] or "",
        "exit_code": task["exit_code"],
        "duration_ms": task["duration_ms"],
        "timed_out": bool(task["timed_out"]),
    }


def serialize_agent(agent: sqlite3.Row, task_count: int = 0) -> dict[str, Any]:
    state = agent_status(agent["last_seen"])
    return {
        "id": agent["id"],
        "display_name": agent["display_name"] or agent["hostname"],
        "hostname": agent["hostname"],
        "username": agent["username"],
        "os": agent["os"],
        "arch": agent["arch"],
        "agent_version": agent["agent_version"],
        "enrolled_at": agent["enrolled_at"],
        "last_seen": agent["last_seen"],
        "age_seconds": age_seconds(agent["last_seen"]),
        "status": state,
        "remote_ip": agent["remote_ip"] or "—",
        "process_id": agent["process_id"],
        "executable": agent["executable"] or "—",
        "connection_type": agent["connection_type"] or "direct",
        "parent_agent_id": agent["parent_agent_id"],
        "sandbox_available": bool(agent["sandbox_available"]),
        "sandbox_runtime": agent["sandbox_runtime"] or "",
        "task_count": task_count,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "project": PROJECT_NAME,
        "team": TEAM_NAME,
        "operator": OPERATOR_NAME,
        "nonce": TEAM_NONCE,
        "version": app.version,
    }


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, forge_session: str | None = Cookie(default=None)) -> HTMLResponse:
    if verify_session_token(forge_session):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"project_name": PROJECT_NAME, "team_name": TEAM_NAME, "error": None},
    )


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(), password: str = Form()) -> HTMLResponse:
    valid = secrets.compare_digest(username, OPERATOR_USERNAME) and secrets.compare_digest(password, OPERATOR_PASSWORD)
    if not valid:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"project_name": PROJECT_NAME, "team_name": TEAM_NAME, "error": "Credenciales inválidas"},
            status_code=401,
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "forge_session",
        make_session_token(username),
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=SESSION_TTL_HOURS * 3600,
        path="/",
    )
    return response


@app.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("forge_session", path="/")
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request, forge_session: str | None = Cookie(default=None)) -> HTMLResponse:
    if not verify_session_token(forge_session):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "project_name": PROJECT_NAME,
            "team_name": TEAM_NAME,
            "operator_name": OPERATOR_NAME,
            "nonce": TEAM_NONCE,
            "active_page": "sessions",
        },
    )


@app.get("/sessions/{agent_id}", response_class=HTMLResponse)
def session_page(request: Request, agent_id: str, forge_session: str | None = Cookie(default=None)) -> HTMLResponse:
    if not verify_session_token(forge_session):
        return RedirectResponse("/login", status_code=303)
    with closing(db_connect()) as conn:
        exists = conn.execute("SELECT 1 FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return templates.TemplateResponse(
        request,
        "session.html",
        {
            "project_name": PROJECT_NAME,
            "team_name": TEAM_NAME,
            "operator_name": OPERATOR_NAME,
            "nonce": TEAM_NONCE,
            "agent_id": agent_id,
            "active_page": "sessions",
        },
    )


@app.get("/api/operator/summary")
def operator_summary(forge_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    require_operator(forge_session)
    with closing(db_connect()) as conn:
        agents = conn.execute("SELECT last_seen FROM agents").fetchall()
        task_counts = conn.execute(
            "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
        ).fetchall()
        recent = conn.execute(
            "SELECT COUNT(*) AS count FROM tasks WHERE created_at >= ?",
            ((utcnow() - timedelta(hours=1)).isoformat(),),
        ).fetchone()["count"]
    states = {"online": 0, "idle": 0, "offline": 0}
    for agent in agents:
        state = agent_status(agent["last_seen"])
        states[state] = states.get(state, 0) + 1
    tasks = {row["status"]: row["count"] for row in task_counts}
    return {
        "sessions": {"total": len(agents), **states},
        "tasks": {
            "pending": tasks.get("pending", 0),
            "dispatched": tasks.get("dispatched", 0),
            "completed": tasks.get("completed", 0),
            "failed": tasks.get("failed", 0),
            "last_hour": recent,
        },
        "generated_at": now_iso(),
    }


@app.get("/api/operator/sessions")
def operator_sessions(forge_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    require_operator(forge_session)
    with closing(db_connect()) as conn:
        rows = conn.execute(
            """
            SELECT a.*, COUNT(t.id) AS task_count
            FROM agents a
            LEFT JOIN tasks t ON t.agent_id = a.id
            GROUP BY a.id
            ORDER BY a.last_seen DESC
            """
        ).fetchall()
    return {"items": [serialize_agent(row, row["task_count"]) for row in rows]}


@app.get("/api/operator/sessions/{agent_id}")
def operator_session(agent_id: str, forge_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    require_operator(forge_session)
    with closing(db_connect()) as conn:
        row = conn.execute(
            """
            SELECT a.*, COUNT(t.id) AS task_count
            FROM agents a
            LEFT JOIN tasks t ON t.agent_id = a.id
            WHERE a.id = ?
            GROUP BY a.id
            """,
            (agent_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return serialize_agent(row, row["task_count"])


@app.patch("/api/operator/sessions/{agent_id}")
def rename_session(agent_id: str, payload: RenameAgentRequest, forge_session: str | None = Cookie(default=None)) -> dict[str, str]:
    require_operator(forge_session)
    display_name = payload.display_name.strip()
    with closing(db_connect()) as conn:
        result = conn.execute("UPDATE agents SET display_name = ? WHERE id = ?", (display_name, agent_id))
        conn.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "updated"}


@app.get("/api/operator/sessions/{agent_id}/tasks")
def operator_tasks(agent_id: str, limit: int = 50, forge_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    require_operator(forge_session)
    limit = max(1, min(limit, 200))
    with closing(db_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE agent_id = ? ORDER BY id DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
    return {"items": [serialize_task(row) for row in rows]}


@app.post("/api/operator/sessions/{agent_id}/tasks", status_code=201)
def operator_create_task(agent_id: str, payload: OperatorTaskRequest, forge_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    require_operator(forge_session)
    command = validate_operator_command(payload.command, payload.mode)
    with closing(db_connect()) as conn:
        agent = conn.execute(
            "SELECT id, sandbox_available FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        if payload.mode == "sandbox" and not bool(agent["sandbox_available"]):
            raise HTTPException(
                status_code=409,
                detail="This agent did not enroll with the isolated lab sandbox enabled",
            )
        cursor = conn.execute(
            "INSERT INTO tasks (agent_id, command, execution_mode, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (agent_id, command, payload.mode, now_iso()),
        )
        task_id = cursor.lastrowid
        conn.commit()
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return serialize_task(task)


@app.post("/api/operator/tasks/{task_id}/cancel")
def operator_cancel_task(task_id: int, forge_session: str | None = Cookie(default=None)) -> dict[str, str]:
    require_operator(forge_session)
    with closing(db_connect()) as conn:
        result = conn.execute(
            "UPDATE tasks SET status = 'cancelled', completed_at = ? WHERE id = ? AND status = 'pending'",
            (now_iso(), task_id),
        )
        conn.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="Only pending tasks can be cancelled")
    return {"status": "cancelled"}


@app.get("/api/operator/events")
def operator_events(forge_session: str | None = Cookie(default=None)) -> StreamingResponse:
    require_operator(forge_session)

    def event_stream():
        previous = None
        while True:
            with closing(db_connect()) as conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(last_seen), ''), COALESCE((SELECT MAX(id) FROM tasks), 0), COALESCE((SELECT MAX(completed_at) FROM tasks), '') FROM agents"
                ).fetchone()
            marker = "|".join(str(value) for value in row)
            if marker != previous:
                yield f"event: refresh\ndata: {json.dumps({'at': now_iso()})}\n\n"
                previous = marker
            else:
                yield f"event: heartbeat\ndata: {json.dumps({'at': now_iso()})}\n\n"
            time.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/agents/enroll", response_model=EnrollResponse)
def enroll_agent(payload: EnrollRequest, request: Request, x_enrollment_token: str | None = Header(default=None)) -> EnrollResponse:
    if not x_enrollment_token or not secrets.compare_digest(x_enrollment_token, ENROLLMENT_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid enrollment token")

    agent_id = f"agt_{secrets.token_hex(8)}"
    agent_token = secrets.token_urlsafe(36)
    timestamp = now_iso()
    remote_ip = request.client.host if request.client else None
    with closing(db_connect()) as conn:
        conn.execute(
            """
            INSERT INTO agents (
                id, token_hash, hostname, username, os, arch, agent_version,
                enrolled_at, last_seen, display_name, remote_ip, process_id, executable,
                connection_type, sandbox_available, sandbox_runtime
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'direct', ?, ?)
            """,
            (
                agent_id,
                token_hash(agent_token),
                payload.hostname,
                payload.username,
                payload.os.lower(),
                payload.arch,
                payload.agent_version,
                timestamp,
                timestamp,
                payload.hostname,
                remote_ip,
                payload.process_id,
                payload.executable,
                1 if payload.sandbox_available else 0,
                payload.sandbox_runtime,
            ),
        )
        conn.commit()
    return EnrollResponse(
        agent_id=agent_id,
        agent_token=agent_token,
        checkin_interval=CHECKIN_INTERVAL_SECONDS,
        command_timeout=COMMAND_TIMEOUT_SECONDS,
        max_output_bytes=MAX_OUTPUT_BYTES,
    )


@app.post("/api/v1/agents/{agent_id}/checkin", response_model=CheckinResponse)
def agent_checkin(
    agent_id: str,
    request: Request,
    payload: HeartbeatRequest | None = Body(default=None),
    authorization: str | None = Header(default=None),
) -> CheckinResponse:
    authenticate_agent(agent_id, authorization)
    timestamp = now_iso()
    remote_ip = request.client.host if request.client else None
    heartbeat = payload or HeartbeatRequest()
    with closing(db_connect()) as conn:
        conn.execute(
            """
            UPDATE agents
            SET last_seen = ?, remote_ip = COALESCE(?, remote_ip),
                agent_version = COALESCE(?, agent_version),
                process_id = COALESCE(?, process_id),
                executable = COALESCE(?, executable),
                sandbox_available = COALESCE(?, sandbox_available),
                sandbox_runtime = COALESCE(?, sandbox_runtime)
            WHERE id = ?
            """,
            (
                timestamp,
                remote_ip,
                heartbeat.agent_version,
                heartbeat.process_id,
                heartbeat.executable,
                1 if heartbeat.sandbox_available else 0 if heartbeat.sandbox_available is not None else None,
                heartbeat.sandbox_runtime,
                agent_id,
            ),
        )
        task = conn.execute(
            """
            SELECT id, command, execution_mode FROM tasks
            WHERE agent_id = ? AND status = 'pending'
            ORDER BY id ASC LIMIT 1
            """,
            (agent_id,),
        ).fetchone()
        if task is not None:
            updated = conn.execute(
                "UPDATE tasks SET status = 'dispatched', dispatched_at = ? WHERE id = ? AND status = 'pending'",
                (timestamp, task["id"]),
            )
            if updated.rowcount == 0:
                task = None
        conn.commit()

    return CheckinResponse(
        task=TaskPayload(
            id=task["id"], command=task["command"], mode=task["execution_mode"] or "host"
        ) if task else None,
        checkin_interval=CHECKIN_INTERVAL_SECONDS,
    )


@app.post("/api/v1/agents/{agent_id}/tasks/{task_id}/result")
def submit_result(
    agent_id: str,
    task_id: int,
    payload: ResultRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    authenticate_agent(agent_id, authorization)
    stdout = payload.stdout[:MAX_OUTPUT_BYTES]
    stderr = payload.stderr[:MAX_OUTPUT_BYTES]
    final_status = "completed" if payload.exit_code == 0 else "failed"
    with closing(db_connect()) as conn:
        task = conn.execute(
            "SELECT id, status FROM tasks WHERE id = ? AND agent_id = ?",
            (task_id, agent_id),
        ).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if task["status"] not in {"dispatched", "pending"}:
            raise HTTPException(status_code=409, detail="Task is not accepting results")
        conn.execute(
            """
            UPDATE tasks
            SET status = ?, completed_at = ?, stdout = ?, stderr = ?,
                exit_code = ?, duration_ms = ?, timed_out = ?
            WHERE id = ?
            """,
            (
                final_status,
                now_iso(),
                stdout,
                stderr,
                payload.exit_code,
                payload.duration_ms,
                1 if payload.timed_out else 0,
                task_id,
            ),
        )
        conn.commit()
    return {"status": "stored"}
