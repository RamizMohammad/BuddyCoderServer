import sqlite3
import tempfile
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pistonpy import PistonApp
import threading
import time

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

# Connect to SQLite (file-based, but you can use ":memory:" for ephemeral)
conn = sqlite3.connect("code_store.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS code_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    language TEXT,
    version TEXT,
    filename TEXT,
    code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

DEFAULT_VERSIONS = {
    "python": "3.10.0",
    "javascript": "18.15.0",
    "java": "15.0.2",
    "c": "10.2.0",
    "cpp": "10.2.0"
}

@app.post("/run")
async def run_code(request: Request):
    data = await request.json()
    language = data.get("language")
    code = data.get("code")

    if not language or not code:
        return JSONResponse({"error": "Language and code are required"}, status_code=400)

    version = DEFAULT_VERSIONS.get(language, "*")

    # Save code in DB
    filename = f"main.{language}"
    cursor.execute("INSERT INTO code_files (language, version, filename, code) VALUES (?, ?, ?, ?)",
                   (language, version, filename, code))
    conn.commit()

    file_id = cursor.lastrowid

    # Retrieve back
    cursor.execute("SELECT filename, code FROM code_files WHERE id=?", (file_id,))
    row = cursor.fetchone()
    file = {"name": row[0], "content": row[1]}

    # Run with Piston
    result = piston.run(
        language=language,
        version=version,
        files=[file]
    )

    # Burn after execution
    cursor.execute("DELETE FROM code_files WHERE id=?", (file_id,))
    conn.commit()

    return JSONResponse(result)

# Dummy self-ping route
@app.get("/ping")
def ping():
    return {"status": "alive"}

def keep_alive():
    while True:
        import requests
        try:
            requests.get("https://your-app.onrender.com/ping")
        except Exception:
            pass
        time.sleep(600)  # ping every 10 mins

threading.Thread(target=keep_alive, daemon=True).start()
