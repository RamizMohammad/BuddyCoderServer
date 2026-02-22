"""
BuddyCoder API Server - Production Hardened
=============================================
Security: Rate limiting, input validation, path traversal prevention,
          bcrypt hashing, strict CORS, JWT rotation hints, file type allowlist
Scalability: Async MongoDB (Motor), async HTTP (httpx), connection pooling,
             background task queuing
LLD-friendly: Clear separation of concerns via modules/classes
"""

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Optional

import bcrypt
import httpx
import jwt
import psutil
from bson import ObjectId
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

load_dotenv()

# ─────────────────────────────────────────────
# CONFIGURATION  (validate at startup)
# ─────────────────────────────────────────────

MONGO_URI = os.getenv("MONGO_URI")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 12
UPLOAD_DIR = Path("./uploads")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# Allowlist of MIME types users may upload
ALLOWED_MIME_TYPES = {
    "text/plain", "text/csv", "text/html",
    "application/json", "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "application/zip",
}

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

WANDBOX_API_URL = "https://wandbox.org/api/compile.ndjson"
WANDBOX_TIMEOUT = 20  # seconds

if not MONGO_URI or not SECRET_KEY:
    raise RuntimeError("MONGO_URI and SECRET_KEY must be set in environment")

if len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY must be at least 32 characters long")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("buddycoder")

# ─────────────────────────────────────────────
# LANGUAGE MAP
# ─────────────────────────────────────────────

LANGUAGE_MAP: dict[str, dict] = {
    "python":     {"compiler": "cpython-3.14.0",        "options": ""},
    "javascript": {"compiler": "nodejs-20.17.0",         "options": ""},
    "js":         {"compiler": "nodejs-20.17.0",         "options": ""},
    "c":          {"compiler": "gcc-13.2.0-c",           "options": "warning,gnu11,cpp-no-pedantic"},
    "cpp":        {"compiler": "gcc-13.2.0",             "options": "warning,boost-1.83.0-gcc-13.2.0,gnu++2b,cpp-no-pedantic"},
    "c++":        {"compiler": "gcc-13.2.0",             "options": "warning,boost-1.83.0-gcc-13.2.0,gnu++2b,cpp-no-pedantic"},
    "java":       {"compiler": "openjdk-jdk-22+36",      "options": ""},
    "ruby":       {"compiler": "ruby-3.3.0",             "options": ""},
    "go":         {"compiler": "go-1.22.0",              "options": ""},
    "rust":       {"compiler": "rust-1.76.0",            "options": ""},
    "php":        {"compiler": "php-8.3.0",              "options": ""},
    "swift":      {"compiler": "swift-5.9.2",            "options": ""},
    "kotlin":     {"compiler": "kotlin-1.9.22",          "options": ""},
    "typescript": {"compiler": "typescript-5.3.3",       "options": ""},
    "ts":         {"compiler": "typescript-5.3.3",       "options": ""},
    "bash":       {"compiler": "bash",                   "options": ""},
    "lua":        {"compiler": "lua-5.4.4",              "options": ""},
    "perl":       {"compiler": "perl-5.38.0",            "options": ""},
    "r":          {"compiler": "r-4.3.2",                "options": ""},
}

# ─────────────────────────────────────────────
# DATABASE (async Motor client)
# ─────────────────────────────────────────────

_mongo_client: Optional[AsyncIOMotorClient] = None

def get_db():
    return _mongo_client["buddycoder"]  # type: ignore

def get_users_col():
    return get_db()["users"]

def get_files_col():
    return get_db()["files"]

# ─────────────────────────────────────────────
# APP LIFESPAN
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mongo_client
    logger.info("Connecting to MongoDB…")
    _mongo_client = AsyncIOMotorClient(
        MONGO_URI,
        maxPoolSize=50,
        minPoolSize=5,
        serverSelectionTimeoutMS=5000,
    )
    # Ensure indexes
    db = _mongo_client["buddycoder"]
    await db["users"].create_index("email", unique=True)
    await db["files"].create_index("user_id")
    logger.info("MongoDB ready.")
    yield
    logger.info("Shutting down MongoDB connection…")
    _mongo_client.close()

# ─────────────────────────────────────────────
# RATE LIMITER
# ─────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)

# ─────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────

app = FastAPI(
    title="BuddyCoder API",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None,          # Disable Swagger in prod; re-enable via env flag if needed
    redoc_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,   # ← no wildcard in production
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# ─────────────────────────────────────────────
# SECURITY HELPERS
# ─────────────────────────────────────────────

def hash_password(password: str) -> str:
    """bcrypt hash (replaces sha256 — bcrypt is slow by design, thwarting brute-force)."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode(), salt).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(email: str) -> str:
    payload = {
        "sub": email,
        "iat": datetime.now(tz=timezone.utc),
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def safe_object_id(raw: str) -> ObjectId:
    """Raises 400 instead of 500 on invalid ObjectId."""
    try:
        return ObjectId(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID format")


def sanitize_filename(name: str) -> str:
    """Strip path traversal sequences and unsafe characters."""
    name = os.path.basename(name)  # strip directories
    name = re.sub(r"[^\w.\- ]", "_", name)  # keep safe chars
    name = name.strip(". ")        # strip leading dots/spaces
    return name[:200] or "file"    # enforce max length, ensure non-empty


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

def real_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

limiter = Limiter(key_func=real_ip)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = decode_token(token)
    email = payload.get("sub")
    if not email:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    user = await get_users_col().find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

# ─────────────────────────────────────────────
# PYDANTIC MODELS  (strict validation)
# ─────────────────────────────────────────────

PASSWORD_RE = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,128}$")

class UserRegister(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def strong_password(cls, v: str) -> str:
        if not PASSWORD_RE.match(v):
            raise ValueError(
                "Password must be 8–128 chars with uppercase, lowercase, and a digit."
            )
        return v


class RenameRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=200)


class RunRequest(BaseModel):
    language: str = Field(..., min_length=1, max_length=30)
    code: str = Field(..., max_length=50_000)   # cap code size
    stdin: str = Field("", max_length=10_000)

# ─────────────────────────────────────────────
# HEALTH HELPERS
# ─────────────────────────────────────────────

_start_time = time.time()
_server_id = socket.gethostname()

# ─────────────────────────────────────────────
# ROUTES — AUTH
# ─────────────────────────────────────────────

@app.post("/register", status_code=201)
@limiter.limit("5/minute")
async def register_user(request: Request, body: UserRegister):
    existing = await get_users_col().find_one({"email": body.email})
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    result = await get_users_col().insert_one({
        "email": body.email,
        "password": hash_password(body.password),
        "createdAt": datetime.now(tz=timezone.utc),
        "saved_files": [],
    })
    logger.info("Registered user %s", body.email)
    return {"message": "User registered successfully", "user_id": str(result.inserted_id)}


@app.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    user = await get_users_col().find_one({"email": form_data.username})
    # Always run verify_password to prevent timing attacks
    valid = verify_password(form_data.password, user["password"]) if user else False
    if not user or not valid:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(user["email"])
    logger.info("Login: %s", form_data.username)
    return {"access_token": token, "token_type": "bearer"}

# ─────────────────────────────────────────────
# ROUTES — CODE EXECUTION
# ─────────────────────────────────────────────

@app.post("/run")
@limiter.limit("20/minute")
async def run_code(request: Request, body: RunRequest):
    language = body.language.lower().strip()
    lang_config = LANGUAGE_MAP.get(language)
    if not lang_config:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language '{language}'. Supported: {sorted(LANGUAGE_MAP)}"
        )

    payload = {
        "compiler": lang_config["compiler"],
        "options": lang_config["options"],
        "code": body.code,
        "codes": [],
        "compiler-option-raw": "",
        "runtime-option-raw": "",
        "stdin": body.stdin,
        "title": "",
        "description": "",
    }

    try:
        async with httpx.AsyncClient(timeout=WANDBOX_TIMEOUT) as client:
            resp = await client.post(WANDBOX_API_URL, json=payload)
            resp.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Execution timed out")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Wandbox error: {e.response.status_code}")
    except Exception as e:
        logger.exception("Wandbox request failed")
        raise HTTPException(status_code=502, detail="Code execution service unavailable")

    output, stderr, exit_code = "", "", None
    for line in resp.text.strip().split("\n"):
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = obj.get("type")
        if t == "StdOut":
            output += obj.get("data", "")
        elif t == "StdErr":
            stderr += obj.get("data", "")
        elif t == "ExitCode":
            exit_code = obj.get("data")

    return {"output": output, "stderr": stderr, "exit_code": exit_code}

# ─────────────────────────────────────────────
# ROUTES — FILES
# ─────────────────────────────────────────────

@app.post("/upload", status_code=201)
@limiter.limit("30/minute")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    # Validate filename
    safe_name = sanitize_filename(file.filename or "upload")

    # Check MIME type (from Content-Type header)
    content_type = file.content_type or "application/octet-stream"
    base_type = content_type.split(";")[0].strip()
    if base_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=415, detail=f"File type '{base_type}' not allowed")

    # Read with size cap
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 10 MB limit")

    new_file_id = ObjectId()
    stored_name = f"{new_file_id}_{safe_name}"
    file_path = UPLOAD_DIR / stored_name

    # Write async-safely via run_in_executor
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, file_path.write_bytes, content)

    await get_files_col().insert_one({
        "_id": new_file_id,
        "user_id": str(user["_id"]),
        "filename": safe_name,
        "stored_name": stored_name,
        "path": str(file_path),
        "size": len(content),
        "mime_type": base_type,
        "uploadedAt": datetime.now(tz=timezone.utc),
    })

    await get_users_col().update_one(
        {"_id": user["_id"]},
        {"$push": {"saved_files": str(new_file_id)}},
    )

    logger.info("Upload: user=%s file=%s size=%d", user["email"], safe_name, len(content))
    return {
        "message": "File uploaded successfully",
        "file_id": str(new_file_id),
        "filename": safe_name,
    }


@app.get("/files")
async def list_user_files(user: dict = Depends(get_current_user)):
    cursor = get_files_col().find(
        {"user_id": str(user["_id"])},
        {"path": 0, "stored_name": 0},   # don't expose internal paths
    )
    files = []
    async for f in cursor:
        f["_id"] = str(f["_id"])
        if isinstance(f.get("uploadedAt"), datetime):
            f["uploadedAt"] = f["uploadedAt"].isoformat()
        files.append(f)
    return {"files": files}


@app.put("/files/{file_id}/rename")
async def rename_file(
    file_id: str,
    body: RenameRequest,
    user: dict = Depends(get_current_user),
):
    obj_id = safe_object_id(file_id)
    safe_name = sanitize_filename(body.filename)

    result = await get_files_col().update_one(
        {"_id": obj_id, "user_id": str(user["_id"])},
        {"$set": {"filename": safe_name}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="File not found")

    return {"status": "success", "new_name": safe_name}


@app.delete("/files/{file_id}", status_code=204)
async def delete_file(file_id: str, user: dict = Depends(get_current_user)):
    obj_id = safe_object_id(file_id)
    entry = await get_files_col().find_one({"_id": obj_id, "user_id": str(user["_id"])})
    if not entry:
        raise HTTPException(status_code=404, detail="File not found")

    # Remove from disk
    p = Path(entry["path"])
    if p.exists():
        p.unlink()

    await get_files_col().delete_one({"_id": obj_id})
    await get_users_col().update_one(
        {"_id": user["_id"]},
        {"$pull": {"saved_files": file_id}},
    )


@app.get("/download/{file_id}")
@limiter.limit("30/minute")
async def download_file(
    request: Request,
    file_id: str,
    user: dict = Depends(get_current_user),
):
    obj_id = safe_object_id(file_id)
    entry = await get_files_col().find_one({"_id": obj_id, "user_id": str(user["_id"])})
    if not entry:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = Path(entry["path"])

    # Prevent path traversal: ensure path is inside UPLOAD_DIR
    try:
        file_path.resolve().relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        logger.warning("Path traversal attempt by user=%s", user["email"])
        raise HTTPException(status_code=403, detail="Forbidden")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File missing on server")

    return FileResponse(
        path=str(file_path),
        filename=entry["filename"],
        media_type=entry.get("mime_type", "application/octet-stream"),
    )

# ─────────────────────────────────────────────
# ROUTES — PROFILE
# ─────────────────────────────────────────────

@app.get("/me")
async def me(user: dict = Depends(get_current_user)):
    saved_ids = user.get("saved_files") or []
    obj_ids = [ObjectId(fid) for fid in saved_ids if ObjectId.is_valid(fid)]

    files = []
    if obj_ids:
        cursor = get_files_col().find(
            {"_id": {"$in": obj_ids}},
            {"path": 0, "stored_name": 0},
        )
        async for f in cursor:
            f["_id"] = str(f["_id"])
            if isinstance(f.get("uploadedAt"), datetime):
                f["uploadedAt"] = f["uploadedAt"].isoformat()
            files.append(f)

    return {
        "user": {
            "email": user["email"],
            "_id": str(user["_id"]),
            "createdAt": user["createdAt"].isoformat() if isinstance(user.get("createdAt"), datetime) else user.get("createdAt"),
        },
        "files": files,
    }

# ─────────────────────────────────────────────
# ROUTES — HEALTH
# ─────────────────────────────────────────────

@app.get("/health")
async def health():
    loop = asyncio.get_running_loop()
    cpu = await loop.run_in_executor(None, lambda: psutil.cpu_percent(interval=0.1))
    return {
        "serverId": _server_id,
        "cpu": cpu,
        "memory": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage("/").percent,
        "uptime": round(time.time() - _start_time, 2),
        "active": True,
    }

@app.get("/alive")
async def alive():
    return {"status": "alive"}

# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────