import threading
import time
from fastapi import FastAPI, Request, BackgroundTasks, Query, Header, Depends, HTTPException, status, File, UploadFile
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

# ---------------- APP & MONGO SETUP ----------------
app = FastAPI()
MONGO_URI = os.environ["MONGO_URI"]
SECRET_KEY = os.environ["SECRET_KEY"]
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
PISTON_API_URL = "https://emkc.org/api/v2/piston/execute"

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
    users_col.insert_one({
        "email": user.email,
        "password": hash_password(user.password),
        "createdAt": datetime.datetime.utcnow()
    })
    return {"message": "User registered successfully"}

@app.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = users_col.find_one({"email": form_data.username})
    if not user or user["password"] != hash_password(form_data.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token({"email": user["email"]})
    return {"access_token": token, "token_type": "bearer"}

# ---------------- CODE EXECUTION ----------------
@app.post("/run")
async def run_code(request: Request):
    try:
        data = await request.json()
        language = data.get("language", "python")
        version = data.get("version", "*")
        code = data.get("code", "")

        payload = {
            "language": language,
            "version": version,
            "files": [{"content": code}]
        }

        response = requests.post(PISTON_API_URL, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# ---------------- FILE ROUTES ----------------
@app.post("/upload")
async def upload_file(file: UploadFile = File(...), user=Depends(get_current_user)):
    filename = f"{user['_id']}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    files_col.insert_one({
        "user_id": str(user["_id"]),
        "filename": file.filename,
        "stored_name": filename,
        "path": file_path,
        "uploadedAt": datetime.datetime.utcnow()
    })
    return {"message": "File uploaded successfully"}

@app.get("/files")
async def list_user_files(user=Depends(get_current_user)):
    files = list(files_col.find({"user_id": str(user["_id"])}))
    for f in files:
        f["_id"] = str(f["_id"])
    return {"files": files}

@app.get("/download/{file_id}")
async def download_file(file_id: str, user=Depends(get_current_user)):
    file_entry = files_col.find_one({"_id": ObjectId(file_id), "user_id": str(user["_id"])})
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_entry["path"], filename=file_entry["filename"])

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

# ---------------- KEEP-ALIVE ----------------
def keep_alive():
    while True:
        try:
            requests.get("https://buddycoderserver-d8iy.onrender.com/alive", timeout=5)
        except Exception:
            pass
        time.sleep(300)

threading.Thread(target=keep_alive, daemon=True).start()
