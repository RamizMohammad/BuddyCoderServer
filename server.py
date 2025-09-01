import threading
import time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import requests
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- Routes ----------------
PISTON_API_URL = "https://emkc.org/api/v2/piston/execute"

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
            "files": [
                {"content": code}
            ]
        }
        
        response = requests.post(PISTON_API_URL, json=payload, timeout=10) 
        response.raise_for_status()
        
        result = response.json()
        return JSONResponse(content=result)

    except requests.exceptions.RequestException as e:
        return JSONResponse(content={"error": f"Failed to connect to execution service: {e}"}, status_code=500)
    except Exception as e:
        return JSONResponse(content={"error": f"An unexpected error occurred: {e}"}, status_code=500)

@app.get("/health")
async def health():
    return {"status": "alive"}

# ---------------- Keep-alive Thread ----------------
def keep_alive():
    while True:
        try:
            requests.get("https://buddycoderserver-d8iy.onrender.com/health", timeout=5)
        except Exception:
            pass
        time.sleep(300)

threading.Thread(target=keep_alive, daemon=True).start()
