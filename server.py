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
    result = users_col.insert_one({
        "email": user.email,
        "password": hash_password(user.password),
        "createdAt": datetime.datetime.utcnow(),
        "saved_files": []   # initialize saved_files array
    })
    return {"message": "User registered successfully", "user_id": str(result.inserted_id)}

@app.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = users_col.find_one({"email": form_data.username})
    if not user or user["password"] != hash_password(form_data.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token({"email": user["email"]})
    return {"access_token": token, "token_type": "bearer"}


@app.put("/files/{file_id}/rename")
async def rename_file(file_id: str, request: Request, user: dict = Depends(get_current_user)):
    """
    Renames a user's file safely by updating the filename field in MongoDB.
    """
    try:
        # Parse new filename from request body
        data = await request.json()
        new_filename = data.get("filename")

        if not new_filename or not new_filename.strip():
            raise HTTPException(status_code=400, detail="Filename cannot be empty")

        try:
            obj_id = ObjectId(file_id)
        except InvalidId:
            raise HTTPException(status_code=400, detail="Invalid file ID format")

        file_entry = files_col.find_one({"_id": obj_id, "user_id": str(user["_id"])})
        if not file_entry:
            raise HTTPException(status_code=404, detail="File not found")

        # Update filename in MongoDB
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
    """
    Uploads file to disk and files collection.
    Also appends the file's ObjectId (as string) to user's `saved_files` array.
    Returns file metadata including the file_id so frontend can reference it.
    """
    # create an ObjectId up-front so we can use it as the _id in the files collection
    new_file_id = ObjectId()
    stored_name = f"{str(new_file_id)}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, stored_name)

    # write file to disk
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    # insert file metadata using the pre-generated ObjectId
    files_col.insert_one({
        "_id": new_file_id,
        "user_id": str(user["_id"]),
        "filename": file.filename,
        "stored_name": stored_name,
        "path": file_path,
        "uploadedAt": datetime.datetime.utcnow()
    })

    # ensure saved_files field exists and push this file id (string) into it
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
    This is a simple list of file metadata from the files collection.
    """
    files = list(files_col.find({"user_id": str(user["_id"])}))
    for f in files:
        f["_id"] = str(f["_id"])
        # convert BSON datetimes to ISO strings if present
        if isinstance(f.get("uploadedAt"), datetime.datetime):
            f["uploadedAt"] = f["uploadedAt"].isoformat()
    return {"files": files}

@app.get("/me")
async def me(user=Depends(get_current_user)):
    """
    Returns user profile and the populated saved files array in a single response.
    Ideal for a side panel to show user info and all files saved by them.
    """
    # Ensure user has saved_files key
    saved_file_ids = user.get("saved_files", []) or []

    # Convert each id string into ObjectId safely
    object_ids = []
    for fid in saved_file_ids:
        try:
            object_ids.append(ObjectId(fid))
        except Exception:
            # skip invalid ids
            pass

    files = []
    if object_ids:
        files_cursor = files_col.find({"_id": {"$in": object_ids}})
        for f in files_cursor:
            f["_id"] = str(f["_id"])
            if isinstance(f.get("uploadedAt"), datetime.datetime):
                f["uploadedAt"] = f["uploadedAt"].isoformat()
            files.append(f)

    # Build user info (don't send password)
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
    except InvalidId:
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

# ---------------- KEEP-ALIVE ----------------
def keep_alive():
    while True:
        try:
            requests.get("https://buddycoderserver.onrender.com/alive", timeout=5)
        except Exception:
            pass
        time.sleep(300)

threading.Thread(target=keep_alive, daemon=True).start()