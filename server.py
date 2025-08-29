import tempfile
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pistonpy import PistonApp
import threading
import time
import requests
import logging

logging.basicConfig(level=logging.INFO)

app = FastAPI()
piston = PistonApp()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # in prod: restrict to frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_VERSIONS = {
    "python": "3.10.0",
    "c": "10.2.0",
    "cpp": "10.2.0",
    "java": "15.0.2",
    "javascript": "18.15.0",
}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/ping")
async def ping():
    return {"status": "alive"}

@app.post("/run")
async def run_code(request: Request):
    try:
        data = await request.json()
        language = data.get("language")
        code = data.get("code")

        if not language or not code:
            return JSONResponse({"error": "Missing language or code"}, status_code=400)

        version = DEFAULT_VERSIONS.get(language.lower())
        if not version:
            return JSONResponse({"error": f"No default version for {language}"}, status_code=400)

        # ✅ create ephemeral temp file (auto-burn)
        ext = get_extension(language)
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext, mode="w", encoding="utf-8") as tmp_file:
            tmp_file.write(code)
            tmp_filename = tmp_file.name

        try:
            result = piston.run(
                language=language,
                version=version,
                files=[tmp_filename]  # pass path
            )
        finally:
            # burn the file right after execution
            if os.path.exists(tmp_filename):
                os.remove(tmp_filename)

        return JSONResponse(content=result)

    except Exception as e:
        logging.exception("Error running code")
        return JSONResponse({"error": str(e)}, status_code=500)


def get_extension(lang: str) -> str:
    extensions = {
        "python": ".py",
        "c": ".c",
        "cpp": ".cpp",
        "java": ".java",
        "javascript": ".js",
    }
    return extensions.get(lang.lower(), ".txt")

# ✅ background keep-alive
def keep_alive():
    url = "https://buddycoderserver.onrender.com/ping"
    while True:
        try:
            requests.get(url, timeout=5)
            logging.info("[KEEP-ALIVE] Pinged %s", url)
        except Exception as e:
            logging.error("[KEEP-ALIVE] Error: %s", e)
        time.sleep(300)

@app.on_event("startup")
def start_keep_alive():
    thread = threading.Thread(target=keep_alive, daemon=True)
    thread.start()
