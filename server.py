import sqlite3
import threading
import time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
# No longer need pistonpy, we'll use requests
import requests 
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()
# piston = PistonApp() # No longer needed

# ... (your CORS and DB setup code remains the same) ...
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
PISTON_API_URL = "https://emkc.org/api/v2/piston/execute"

@app.post("/run")
async def run_code(request: Request):
    data = await request.json()
    language = data.get("language", "python")
    version = data.get("version", "*")  # You might want to get a specific version
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

    # --- REVISED CODE ---
    # Create the payload for the Piston API
    payload = {
        "language": language,
        "version": version,
        "files": [
            {"content": code}
        ]
    }
    
    # Make the API call directly
    response = requests.post(PISTON_API_URL, json=payload)
    result = response.json()
    # --- END REVISED CODE ---

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

