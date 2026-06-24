from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
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
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ARTIFACT_DIR = Path("/artifacts") if Path("/artifacts").exists() else BASE_DIR.parent.parent / "bin"
ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", str(DEFAULT_ARTIFACT_DIR)))
ARTIFACTS = {
    "linux": {
        "filename": "cybersen-forge-linux-amd64",
        "label": "Linux AMD64",
        "media_type": "application/octet-stream",
    },
    "windows": {
        "filename": "cybersen-forge-windows-amd64.exe",
        "label": "Windows AMD64",
        "media_type": "application/vnd.microsoft.portable-executable",
    },
}

app = FastAPI(title=PROJECT_NAME, version="0.5.0", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


class NetworkInfo(BaseModel):
    interface: str = Field(min_length=1, max_length=64)
    address: str = Field(min_length=1, max_length=64)
    network: str = Field(min_length=1, max_length=64)


class PivotStatusRequest(BaseModel):
    id: int = Field(gt=0)
    status: str = Field(min_length=1, max_length=32)
    error: str = Field(default="", max_length=2048)


class PivotInstruction(BaseModel):
    id: int
    listen_port: int
    target_host: str
    target_port: int


class PivotCreateRequest(BaseModel):
    network: str = Field(min_length=1, max_length=64)
    target_host: str = Field(min_length=1, max_length=64)


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
    networks: list[NetworkInfo] = Field(default_factory=list)
    pivot_available: bool = False


class EnrollResponse(BaseModel):
    agent_id: str
    agent_token: str
    checkin_interval: int
    command_timeout: int
    max_output_bytes: int


class TaskPayload(BaseModel):
    id: int
    command: str
    mode: Literal["host", "shell", "sandbox"] = "host"


class CheckinResponse(BaseModel):
    task: TaskPayload | None = None
    pivots: list[PivotInstruction] = Field(default_factory=list)
    checkin_interval: int


class HeartbeatRequest(BaseModel):
    agent_version: str | None = Field(default=None, max_length=64)
    process_id: int | None = Field(default=None, ge=0)
    executable: str | None = Field(default=None, max_length=1024)
    sandbox_available: bool | None = None
    sandbox_runtime: str | None = Field(default=None, max_length=32)
    networks: list[NetworkInfo] = Field(default_factory=list)
    pivot_available: bool | None = None
    pivot_statuses: list[PivotStatusRequest] = Field(default_factory=list)


class ResultRequest(BaseModel):
    stdout: str = ""
    stderr: str = ""
    exit_code: int
    duration_ms: int = Field(ge=0)
    timed_out: bool = False


class OperatorTaskRequest(BaseModel):
    command: str = Field(min_length=1, max_length=2048)
    mode: Literal["host", "shell", "sandbox"] = "host"


class RenameAgentRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


class EnrollmentTokenRequest(BaseModel):
    platform: Literal["linux", "windows"]
    ttl_seconds: int = Field(default=600, ge=60, le=3600)
    max_uses: int = Field(default=1, ge=1, le=10)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_platform(value: str) -> str:
    normalized = value.lower().strip()
    if normalized.startswith("win"):
        return "windows"
    if normalized.startswith("linux"):
        return "linux"
    return normalized


def agent_release_version() -> str:
    version_file = ARTIFACT_DIR / "AGENT_VERSION"
    try:
        value = version_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    return "0.3.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_path(platform: str) -> Path:
    metadata = ARTIFACTS.get(platform)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    path = ARTIFACT_DIR / metadata["filename"]
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Agent artifact is not available on this server")
    return path


def validate_temporary_enrollment_token(
    conn: sqlite3.Connection, supplied_token: str, platform: str, *, consume: bool
) -> None:
    row = conn.execute(
        "SELECT * FROM enrollment_tokens WHERE token_hash = ?",
        (token_hash(supplied_token),),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid enrollment token")
    if bool(row["revoked"]):
        raise HTTPException(status_code=401, detail="Enrollment token has been revoked")
    try:
        expired = parse_iso(row["expires_at"]) <= utcnow()
    except ValueError:
        expired = True
    if expired:
        raise HTTPException(status_code=401, detail="Enrollment token has expired")
    if row["platform"] != platform:
        raise HTTPException(status_code=401, detail="Enrollment token is for a different platform")
    if int(row["uses"]) >= int(row["max_uses"]):
        raise HTTPException(status_code=401, detail="Enrollment token has already been used")
    if consume:
        result = conn.execute(
            """
            UPDATE enrollment_tokens
            SET uses = uses + 1
            WHERE id = ? AND revoked = 0 AND uses < max_uses
            """,
            (row["id"],),
        )
        if result.rowcount != 1:
            raise HTTPException(status_code=401, detail="Enrollment token is no longer available")


def authorize_enrollment_credential(
    conn: sqlite3.Connection, supplied_token: str | None, platform: str, *, consume: bool
) -> None:
    if not supplied_token:
        raise HTTPException(status_code=401, detail="Missing enrollment token")
    if secrets.compare_digest(supplied_token, ENROLLMENT_TOKEN):
        return
    validate_temporary_enrollment_token(conn, supplied_token, platform, consume=consume)


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

            CREATE TABLE IF NOT EXISTS pivots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                target_network TEXT NOT NULL,
                target_host TEXT NOT NULL,
                target_port INTEGER NOT NULL DEFAULT 22,
                listen_port INTEGER NOT NULL UNIQUE,
                desired_state TEXT NOT NULL DEFAULT 'active',
                status TEXT NOT NULL DEFAULT 'requested',
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS enrollment_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                platform TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                max_uses INTEGER NOT NULL DEFAULT 1,
                uses INTEGER NOT NULL DEFAULT 0,
                revoked INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_agents_last_seen ON agents(last_seen DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_agent_created ON tasks(agent_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_pivots_agent ON pivots(agent_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_enrollment_tokens_expires ON enrollment_tokens(expires_at);
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
        ensure_column(conn, "agents", "networks_json", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(conn, "agents", "pivot_available", "INTEGER NOT NULL DEFAULT 0")
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
    maximum = 8192 if mode in {"shell", "sandbox"} else 512
    if len(stripped) > maximum:
        raise HTTPException(status_code=400, detail="Command is too long")
    if "\x00" in stripped or "\r" in stripped or "\n" in stripped:
        raise HTTPException(status_code=400, detail="Command contains invalid control characters")
    return stripped if mode in {"shell", "sandbox"} else " ".join(stripped.split())


def decode_networks(value: str | None) -> list[dict[str, str]]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    items: list[dict[str, str]] = []
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict):
            continue
        interface = str(item.get("interface", "")).strip()
        address = str(item.get("address", "")).strip()
        network = str(item.get("network", "")).strip()
        if interface and address and network:
            items.append({"interface": interface, "address": address, "network": network})
    return items


def serialize_pivot(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "network": row["target_network"],
        "target_host": row["target_host"],
        "target_port": row["target_port"],
        "listen_host": "127.0.0.1",
        "listen_port": row["listen_port"],
        "desired_state": row["desired_state"],
        "status": row["status"],
        "last_error": row["last_error"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def allocate_pivot_port(conn: sqlite3.Connection) -> int:
    used = {row["listen_port"] for row in conn.execute("SELECT listen_port FROM pivots")}
    for port in range(22000, 23000):
        if port not in used:
            return port
    raise HTTPException(status_code=409, detail="No pivot listener ports are available")


def validate_pivot_target(agent: sqlite3.Row, selected_network: str, target_host: str) -> tuple[str, str]:
    try:
        network = ipaddress.ip_network(selected_network, strict=False)
        target = ipaddress.ip_address(target_host)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid network or target address") from exc
    if network.version != 4 or target.version != 4:
        raise HTTPException(status_code=400, detail="The pivot MVP only supports IPv4")
    advertised = {item["network"] for item in decode_networks(agent["networks_json"])}
    if str(network) not in advertised:
        raise HTTPException(status_code=400, detail="The selected network is not directly connected to this agent")
    if target not in network or target in {network.network_address, network.broadcast_address}:
        raise HTTPException(status_code=400, detail="Target is outside the selected network")
    return str(network), str(target)


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
        "networks": decode_networks(agent["networks_json"]),
        "pivot_available": bool(agent["pivot_available"]),
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


@app.get("/agents", response_class=HTMLResponse)
def agents_page(request: Request, forge_session: str | None = Cookie(default=None)) -> HTMLResponse:
    if not verify_session_token(forge_session):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "agents.html",
        {
            "project_name": PROJECT_NAME,
            "team_name": TEAM_NAME,
            "operator_name": OPERATOR_NAME,
            "nonce": TEAM_NONCE,
            "active_page": "agents",
        },
    )


@app.get("/pivoting", response_class=HTMLResponse)
def pivoting_page(request: Request, forge_session: str | None = Cookie(default=None)) -> HTMLResponse:
    if not verify_session_token(forge_session):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "pivoting.html",
        {
            "project_name": PROJECT_NAME,
            "team_name": TEAM_NAME,
            "operator_name": OPERATOR_NAME,
            "nonce": TEAM_NONCE,
            "active_page": "pivoting",
        },
    )


@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, forge_session: str | None = Cookie(default=None)) -> HTMLResponse:
    if not verify_session_token(forge_session):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "project_name": PROJECT_NAME,
            "team_name": TEAM_NAME,
            "operator_name": OPERATOR_NAME,
            "nonce": TEAM_NONCE,
            "active_page": "audit",
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


@app.get("/api/operator/artifacts")
def operator_artifacts(
    request: Request, forge_session: str | None = Cookie(default=None)
) -> dict[str, Any]:
    require_operator(forge_session)
    items = []
    for platform, metadata in ARTIFACTS.items():
        path = ARTIFACT_DIR / metadata["filename"]
        available = path.is_file()
        items.append(
            {
                "platform": platform,
                "label": metadata["label"],
                "filename": metadata["filename"],
                "download_url": f"/downloads/{metadata['filename']}",
                "available": available,
                "size_bytes": path.stat().st_size if available else 0,
                "sha256": sha256_file(path) if available else "",
            }
        )
    return {
        "version": agent_release_version(),
        "items": items,
        "public_base_url": PUBLIC_BASE_URL,
        "request_base_url": str(request.base_url).rstrip("/"),
    }


@app.post("/api/operator/enrollment-tokens", status_code=201)
def operator_create_enrollment_token(
    payload: EnrollmentTokenRequest,
    forge_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    require_operator(forge_session)
    token = "forge_enroll_" + secrets.token_urlsafe(24)
    created_at = utcnow()
    expires_at = created_at + timedelta(seconds=payload.ttl_seconds)
    with closing(db_connect()) as conn:
        conn.execute(
            "DELETE FROM enrollment_tokens WHERE expires_at < ? OR revoked = 1",
            (created_at.isoformat(),),
        )
        cursor = conn.execute(
            """
            INSERT INTO enrollment_tokens (
                token_hash, platform, created_at, expires_at, max_uses, uses, revoked
            ) VALUES (?, ?, ?, ?, ?, 0, 0)
            """,
            (
                token_hash(token),
                payload.platform,
                created_at.isoformat(),
                expires_at.isoformat(),
                payload.max_uses,
            ),
        )
        conn.commit()
    return {
        "id": cursor.lastrowid,
        "token": token,
        "platform": payload.platform,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "max_uses": payload.max_uses,
    }


@app.get("/downloads/{filename}")
def download_agent_artifact(
    filename: str,
    forge_session: str | None = Cookie(default=None),
    x_enrollment_token: str | None = Header(default=None),
) -> FileResponse:
    platform = next(
        (name for name, metadata in ARTIFACTS.items() if metadata["filename"] == filename),
        None,
    )
    if platform is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if not verify_session_token(forge_session):
        with closing(db_connect()) as conn:
            authorize_enrollment_credential(
                conn, x_enrollment_token, platform, consume=False
            )
    path = artifact_path(platform)
    metadata = ARTIFACTS[platform]
    return FileResponse(
        path=path,
        filename=metadata["filename"],
        media_type=metadata["media_type"],
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
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
        if payload.mode in {"shell", "sandbox"} and not bool(agent["sandbox_available"]):
            raise HTTPException(
                status_code=409,
                detail="This agent did not enroll with the persistent isolated lab shell enabled",
            )
        cursor = conn.execute(
            "INSERT INTO tasks (agent_id, command, execution_mode, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (agent_id, command, payload.mode, now_iso()),
        )
        task_id = cursor.lastrowid
        conn.commit()
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return serialize_task(task)


@app.get("/api/operator/audit/tasks")
def operator_audit_tasks(
    agent_id: str = "",
    task_status: str = "",
    limit: int = 200,
    forge_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    require_operator(forge_session)
    limit = max(1, min(limit, 500))
    clauses: list[str] = []
    parameters: list[Any] = []
    if agent_id:
        clauses.append("t.agent_id = ?")
        parameters.append(agent_id)
    if task_status:
        clauses.append("t.status = ?")
        parameters.append(task_status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    parameters.append(limit)
    with closing(db_connect()) as conn:
        rows = conn.execute(
            f"""
            SELECT t.*, a.hostname AS agent_hostname, a.display_name AS agent_display_name,
                   a.username AS agent_username, a.os AS agent_os
            FROM tasks t
            JOIN agents a ON a.id = t.agent_id
            {where}
            ORDER BY t.id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    items = []
    for row in rows:
        item = serialize_task(row)
        item["agent"] = {
            "id": row["agent_id"],
            "hostname": row["agent_hostname"],
            "display_name": row["agent_display_name"] or row["agent_hostname"],
            "username": row["agent_username"],
            "os": row["agent_os"],
        }
        items.append(item)
    return {"items": items}


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


@app.get("/api/operator/sessions/{agent_id}/pivots")
def operator_pivots(agent_id: str, forge_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    require_operator(forge_session)
    with closing(db_connect()) as conn:
        agent = conn.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        rows = conn.execute(
            "SELECT * FROM pivots WHERE agent_id = ? ORDER BY id DESC", (agent_id,)
        ).fetchall()
    return {"items": [serialize_pivot(row) for row in rows]}


@app.get("/api/operator/pivots")
def operator_all_pivots(forge_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    require_operator(forge_session)
    with closing(db_connect()) as conn:
        rows = conn.execute(
            """
            SELECT p.*, a.hostname AS agent_hostname, a.display_name AS agent_display_name,
                   a.username AS agent_username, a.os AS agent_os, a.last_seen AS agent_last_seen
            FROM pivots p
            JOIN agents a ON a.id = p.agent_id
            ORDER BY p.id DESC
            """
        ).fetchall()
    items = []
    for row in rows:
        item = serialize_pivot(row)
        item["agent"] = {
            "id": row["agent_id"],
            "hostname": row["agent_hostname"],
            "display_name": row["agent_display_name"] or row["agent_hostname"],
            "username": row["agent_username"],
            "os": row["agent_os"],
            "status": agent_status(row["agent_last_seen"]),
        }
        items.append(item)
    return {"items": items}


@app.post("/api/operator/sessions/{agent_id}/pivots", status_code=201)
def operator_create_pivot(
    agent_id: str,
    payload: PivotCreateRequest,
    forge_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    require_operator(forge_session)
    with closing(db_connect()) as conn:
        agent = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not bool(agent["pivot_available"]):
            raise HTTPException(status_code=409, detail="This agent has no configured SSH pivot transport")
        network, target = validate_pivot_target(agent, payload.network, payload.target_host)
        existing = conn.execute(
            "SELECT * FROM pivots WHERE agent_id = ? AND target_host = ? AND target_port = 22 AND desired_state = 'active'",
            (agent_id, target),
        ).fetchone()
        if existing is not None:
            return serialize_pivot(existing)
        timestamp = now_iso()
        listen_port = allocate_pivot_port(conn)
        cursor = conn.execute(
            """
            INSERT INTO pivots (
                agent_id, target_network, target_host, target_port, listen_port,
                desired_state, status, created_at, updated_at
            ) VALUES (?, ?, ?, 22, ?, 'active', 'requested', ?, ?)
            """,
            (agent_id, network, target, listen_port, timestamp, timestamp),
        )
        pivot_id = cursor.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM pivots WHERE id = ?", (pivot_id,)).fetchone()
    return serialize_pivot(row)


@app.post("/api/operator/pivots/{pivot_id}/start")
def operator_start_pivot(pivot_id: int, forge_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    require_operator(forge_session)
    with closing(db_connect()) as conn:
        result = conn.execute(
            "UPDATE pivots SET desired_state = 'active', status = 'requested', last_error = NULL, updated_at = ? WHERE id = ?",
            (now_iso(), pivot_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM pivots WHERE id = ?", (pivot_id,)).fetchone()
    if result.rowcount == 0 or row is None:
        raise HTTPException(status_code=404, detail="Pivot not found")
    return serialize_pivot(row)


@app.post("/api/operator/pivots/{pivot_id}/stop")
def operator_stop_pivot(pivot_id: int, forge_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    require_operator(forge_session)
    with closing(db_connect()) as conn:
        result = conn.execute(
            "UPDATE pivots SET desired_state = 'stopped', status = 'stopping', updated_at = ? WHERE id = ?",
            (now_iso(), pivot_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM pivots WHERE id = ?", (pivot_id,)).fetchone()
    if result.rowcount == 0 or row is None:
        raise HTTPException(status_code=404, detail="Pivot not found")
    return serialize_pivot(row)


@app.get("/api/operator/events")
def operator_events(forge_session: str | None = Cookie(default=None)) -> StreamingResponse:
    require_operator(forge_session)

    def event_stream():
        previous = None
        while True:
            with closing(db_connect()) as conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(last_seen), ''), COALESCE((SELECT MAX(id) FROM tasks), 0), COALESCE((SELECT MAX(completed_at) FROM tasks), ''), COALESCE((SELECT MAX(updated_at) FROM pivots), '') FROM agents"
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
def enroll_agent(
    payload: EnrollRequest,
    request: Request,
    x_enrollment_token: str | None = Header(default=None),
) -> EnrollResponse:
    platform = normalize_platform(payload.os)
    if platform not in ARTIFACTS:
        raise HTTPException(status_code=400, detail="Unsupported agent platform")

    agent_id = f"agt_{secrets.token_hex(8)}"
    agent_token = secrets.token_urlsafe(36)
    timestamp = now_iso()
    remote_ip = request.client.host if request.client else None
    with closing(db_connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        authorize_enrollment_credential(
            conn, x_enrollment_token, platform, consume=True
        )
        conn.execute(
            """
            INSERT INTO agents (
                id, token_hash, hostname, username, os, arch, agent_version,
                enrolled_at, last_seen, display_name, remote_ip, process_id, executable,
                connection_type, sandbox_available, sandbox_runtime, networks_json, pivot_available
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'direct', ?, ?, ?, ?)
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
                json.dumps([item.model_dump() for item in payload.networks]),
                1 if payload.pivot_available else 0,
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
                sandbox_runtime = COALESCE(?, sandbox_runtime),
                networks_json = ?,
                pivot_available = COALESCE(?, pivot_available)
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
                json.dumps([item.model_dump() for item in heartbeat.networks]),
                1 if heartbeat.pivot_available else 0 if heartbeat.pivot_available is not None else None,
                agent_id,
            ),
        )
        for pivot_status in heartbeat.pivot_statuses:
            normalized = pivot_status.status.lower().strip()
            if normalized not in {"requested", "active", "failed", "stopped", "stopping"}:
                normalized = "failed"
            conn.execute(
                """
                UPDATE pivots
                SET status = ?, last_error = ?, updated_at = ?
                WHERE id = ? AND agent_id = ?
                """,
                (normalized, pivot_status.error or None, timestamp, pivot_status.id, agent_id),
            )
        pivot_rows = conn.execute(
            """
            SELECT id, listen_port, target_host, target_port
            FROM pivots
            WHERE agent_id = ? AND desired_state = 'active'
            ORDER BY id ASC
            """,
            (agent_id,),
        ).fetchall()

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
        pivots=[
            PivotInstruction(
                id=row["id"], listen_port=row["listen_port"],
                target_host=row["target_host"], target_port=row["target_port"],
            )
            for row in pivot_rows
        ],
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
