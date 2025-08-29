import sqlite3
import threading
import time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pistonpy import PistonApp
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()
piston = PistonApp()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- SQLite Setup ----------------
def init_db():
    conn = sqlite3.connect("code_storage.db")
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS code_storage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            language TEXT,
            version TEXT,
            code TEXT
        )"""
    )
    conn.commit()
    conn.close()

init_db()

# ---------------- Routes ----------------
@app.post("/run")
async def run_code(request: Request):
    data = await request.json()
    language = data.get("language", "python")
    version = data.get("version", "*")
    code = data.get("code", "")

    # Save code temporarily in SQLite
    conn = sqlite3.connect("code_storage.db")
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO code_storage (language, version, code) VALUES (?, ?, ?)",
        (language, version, code),
    )
    code_id = cur.lastrowid
    conn.commit()
    conn.close()

    # Run code using Piston
    file = {"name": f"main.{language}", "content": code}
    result = piston.run(language=language, version=version, files=[file])

    # Delete after execution (burn after use)
    conn = sqlite3.connect("code_storage.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM code_storage WHERE id=?", (code_id,))
    conn.commit()
    conn.close()

    return JSONResponse(content=result)

@app.get("/health")
async def health():
    return {"status": "alive"}

# ---------------- Keep-alive Thread ----------------
def keep_alive():
    while True:
        try:
            import requests
            requests.get("http://localhost:8000/health")
        except Exception:
            pass
        time.sleep(300)  # Ping every 5 minutes

threading.Thread(target=keep_alive, daemon=True).start()

