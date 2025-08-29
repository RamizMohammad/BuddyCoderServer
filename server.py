import threading
import time
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pistonpy import PistonApp

app = FastAPI()
piston = PistonApp()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_VERSIONS = {
    "python": "3.10.0",
    "c": "10.2.0",
    "cpp": "10.2.0",
    "java": "15.0.2",
    "javascript": "18.15.0"
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

        # ✅ Pass a dict for the file
        ext = get_extension(language)
        file = {"name": f"Main{ext}", "content": code}

        result = piston.run(
            language=language,
            version=version,
            files=[file]
        )

        return JSONResponse(content=result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


def get_extension(lang: str) -> str:
    extensions = {
        "python": ".py",
        "c": ".c",
        "cpp": ".cpp",
        "java": ".java",
        "javascript": ".js"
    }
    return extensions.get(lang.lower(), ".txt")


# ✅ Background thread to self-ping
def keep_alive():
    url = "https://buddycoderserver.onrender.com/ping" 
    while True:
        try:
            requests.get(url, timeout=5)
            print("[KEEP-ALIVE] Pinged", url)
        except Exception as e:
            print("[KEEP-ALIVE] Error:", e)
        time.sleep(300)  # every 5 min


threading.Thread(target=keep_alive, daemon=True).start()
