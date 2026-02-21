import threading
import time
from fastapi import FastAPI, Request, BackgroundTasks, Query, Header, Depends, HTTPException, status, File, UploadFile, Body
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
import uvicorn
import datetime
import os
import psutil
import socket
import json
import httpx
import requests
import jwt
import hashlib
from pymongo import MongoClient
from bson import ObjectId
import dotenv

dotenv.load_dotenv()

# ---------------- APP & MONGO SETUP ----------------
app = FastAPI()
MONGO_URI = os.getenv("MONGO_URI")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
UPLOAD_DIR = "./uploads"

os.makedirs(UPLOAD_DIR, exist_ok=True)

mongo_client = MongoClient(MONGO_URI)
db = mongo_client["buddycoder"]
users_col = db["users"]
files_col = db["files"]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# ---------------- BASIC MIDDLEWARE ----------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- HEALTH VARIABLES ----------------
serverId = socket.gethostname()
process = psutil.Process(os.getpid())
startTime = time.time()
WANDBOX_API_URL = "https://wandbox.org/api/compile.ndjson"

# ---------------- LANGUAGE MAP ----------------
LANGUAGE_MAP = {
    "python":       {"compiler": "cpython-3.14.0",      "options": ""},
    "javascript":   {"compiler": "nodejs-20.17.0",      "options": ""},
    "js":           {"compiler": "nodejs-20.17.0",      "options": ""},
    "c":            {"compiler": "gcc-13.2.0-c",        "options": "warning,gnu11,cpp-no-pedantic"},
    "cpp":          {"compiler": "gcc-13.2.0",          "options": "warning,boost-1.83.0-gcc-13.2.0,gnu++2b,cpp-no-pedantic"},
    "c++":          {"compiler": "gcc-13.2.0",          "options": "warning,boost-1.83.0-gcc-13.2.0,gnu++2b,cpp-no-pedantic"},
    "java":         {"compiler": "openjdk-jdk-22+36",   "options": ""},
    "ruby":         {"compiler": "ruby-3.3.0",          "options": ""},
    "go":           {"compiler": "go-1.22.0",           "options": ""},
    "rust":         {"compiler": "rust-1.76.0",         "options": ""},
    "php":          {"compiler": "php-8.3.0",           "options": ""},
    "swift":        {"compiler": "swift-5.9.2",         "options": ""},
    "kotlin":       {"compiler": "kotlin-1.9.22",       "options": ""},
    "typescript":   {"compiler": "typescript-5.3.3",    "options": ""},
    "ts":           {"compiler": "typescript-5.3.3",    "options": ""},
    "bash":         {"compiler": "bash",                "options": ""},
    "lua":          {"compiler": "lua-5.4.4",           "options": ""},
    "perl":         {"compiler": "perl-5.38.0",         "options": ""},
    "r":            {"compiler": "r-4.3.2",             "options": ""},
}

# ---------------- AUTH & MODELS ----------------
class User(BaseModel):
    email: str
    password: str

def hash_password(password: str):
    return hashlib.sha256(password.encode()).hexdigest()

def create_token(data: dict):
    payload = data.copy()
    payload["exp"] = datetime.datetime.utcnow() + datetime.timedelta(hours=12)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    user_data = decode_token(token)
    user = users_col.find_one({"email": user_data["email"]})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

# ---------------- AUTH ROUTES ----------------
@app.post("/register")
async def register_user(user: User):
    if users_col.find_one({"email": user.email}):
        raise HTTPException(status_code=400, detail="User already exists")
    result = users_col.insert_one({
        "email": user.email,
        "password": hash_password(user.password),
        "createdAt": datetime.datetime.utcnow(),
        "saved_files": []
    })
    return {"message": "User registered successfully", "user_id": str(result.inserted_id)}

@app.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = users_col.find_one({"email": form_data.username})
    if not user or user["password"] != hash_password(form_data.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token({"email": user["email"]})
    return {"access_token": token, "token_type": "bearer"}

# ---------------- FILE RENAME ----------------
@app.put("/files/{file_id}/rename")
async def rename_file(file_id: str, request: Request, user: dict = Depends(get_current_user)):
    """
    Renames a user's file safely by updating the filename field in MongoDB.
    """
    try:
        data = await request.json()
        new_filename = data.get("filename")

        if not new_filename or not new_filename.strip():
            raise HTTPException(status_code=400, detail="Filename cannot be empty")

        try:
            obj_id = ObjectId(file_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid file ID format")

        file_entry = files_col.find_one({"_id": obj_id, "user_id": str(user["_id"])})
        if not file_entry:
            raise HTTPException(status_code=404, detail="File not found")

        files_col.update_one(
            {"_id": obj_id},
            {"$set": {"filename": new_filename}}
        )

        return {"status": "success", "message": "File renamed successfully", "new_name": new_filename}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------------- CODE EXECUTION ----------------
@app.post("/run")
async def run_code(request: Request):
    try:
        data = await request.json()
        language = data.get("language", "python").lower().strip()
        code = data.get("code", "")

        lang_config = LANGUAGE_MAP.get(language)
        if not lang_config:
            return JSONResponse(
                content={"error": f"Unsupported language: '{language}'. Supported: {list(LANGUAGE_MAP.keys())}"},
                status_code=400
            )

        payload = {
            "compiler": lang_config["compiler"],
            "options": lang_config["options"],
            "code": code,
            "codes": [],
            "compiler-option-raw": "",
            "runtime-option-raw": "",
            "stdin": data.get("stdin", ""),
            "title": "",
            "description": ""
        }

        response = requests.post(WANDBOX_API_URL, json=payload, timeout=15)
        response.raise_for_status()

        output = ""
        stderr = ""
        exit_code = None

        for line in response.text.strip().split("\n"):
            if line:
                obj = json.loads(line)
                if obj.get("type") == "StdOut":
                    output += obj["data"]
                elif obj.get("type") == "StdErr":
                    stderr += obj["data"]
                elif obj.get("type") == "ExitCode":
                    exit_code = obj["data"]

        return JSONResponse(content={
            "output": output,
            "stderr": stderr,
            "exit_code": exit_code
        })

    except Exception as e:
        print(str(e))
        return JSONResponse(content={"error": str(e)}, status_code=500)

# ---------------- FILE ROUTES ----------------
@app.post("/upload")
async def upload_file(file: UploadFile = File(...), user=Depends(get_current_user)):
    """
    Uploads file to disk and files collection.
    Also appends the file's ObjectId (as string) to user's saved_files array.
    """
    new_file_id = ObjectId()
    stored_name = f"{str(new_file_id)}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, stored_name)

    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    files_col.insert_one({
        "_id": new_file_id,
        "user_id": str(user["_id"]),
        "filename": file.filename,
        "stored_name": stored_name,
        "path": file_path,
        "uploadedAt": datetime.datetime.utcnow()
    })

    users_col.update_one(
        {"_id": user["_id"]},
        {"$push": {"saved_files": str(new_file_id)}}
    )

    return {
        "message": "File uploaded successfully",
        "file_id": str(new_file_id),
        "filename": file.filename,
        "stored_name": stored_name
    }

@app.get("/files")
async def list_user_files(user=Depends(get_current_user)):
    """
    Returns the files that belong to the current user.
    """
    files = list(files_col.find({"user_id": str(user["_id"])}))
    for f in files:
        f["_id"] = str(f["_id"])
        if isinstance(f.get("uploadedAt"), datetime.datetime):
            f["uploadedAt"] = f["uploadedAt"].isoformat()
    return {"files": files}

@app.get("/me")
async def me(user=Depends(get_current_user)):
    """
    Returns user profile and the populated saved files array in a single response.
    """
    saved_file_ids = user.get("saved_files", []) or []

    object_ids = []
    for fid in saved_file_ids:
        try:
            object_ids.append(ObjectId(fid))
        except Exception:
            pass

    files = []
    if object_ids:
        files_cursor = files_col.find({"_id": {"$in": object_ids}})
        for f in files_cursor:
            f["_id"] = str(f["_id"])
            if isinstance(f.get("uploadedAt"), datetime.datetime):
                f["uploadedAt"] = f["uploadedAt"].isoformat()
            files.append(f)

    user_info = {
        "email": user.get("email"),
        "_id": str(user.get("_id")),
        "createdAt": user.get("createdAt").isoformat() if isinstance(user.get("createdAt"), datetime.datetime) else user.get("createdAt"),
        "saved_files": saved_file_ids
    }

    return {"user": user_info, "files": files}

@app.get("/download/{file_id}")
async def download_file(file_id: str, user: dict = Depends(get_current_user)):
    if not file_id or file_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid or missing file_id")

    try:
        obj_id = ObjectId(file_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file ID format")

    file_entry = files_col.find_one({"_id": obj_id, "user_id": str(user["_id"])})
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = file_entry["path"]
    filename = file_entry["filename"]

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File missing on server")

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/octet-stream"
    )

# ---------------- HEALTH ----------------
def collect_health_data():
    cpu = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    uptime = round(time.time() - startTime, 2)
    return {
        "serverId": serverId,
        "cpu": cpu,
        "memory": memory,
        "disk": disk,
        "uptime": uptime,
        "active": True
    }

@app.get("/health")
async def health():
    health_data = await run_in_threadpool(collect_health_data)
    return JSONResponse(content=health_data)

@app.get("/alive")
async def alive():
    return {"status": "alive"}

if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=443,
        ssl_certfile="/etc/letsencrypt/live/api.server.buddycode.online/fullchain.pem",
        ssl_keyfile="/etc/letsencrypt/live/api.server.buddycode.online/privkey.pem",
        workers=2
    )