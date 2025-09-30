import threading
import time
from fastapi import FastAPI, Request, BackgroundTasks, Query, Header, Depends
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

async def cors_health_preflight(
    request: Request,
    origin: str = Header(default="*"),
    access_control_request_method: str = Header(default=""),
    access_control_request_headers: str = Header(default="*"),
):
    if request.method == "OPTIONS":
        return JSONResponse(
            status_code=200,
            content={},
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": access_control_request_headers,
                "Access-Control-Max-Age": "86400"
            }
        )

def collect_health_data():
    cpu = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    load_avg = os.getloadavg() if hasattr(os, 'getloadavg') else (0.0, 0.0, 0.0)
    uptime = round(time.time() - startTime, 2)
    threads = process.num_threads()
    process_memory = round(process.memory_info().rss / (1024 ** 2), 2)

    return {
        "serverId": serverId,
        "cpu": cpu,
        "memory": memory,
        "disk": disk,
        "uptime": uptime,
        "loadAvg": {
            "1m": round(load_avg[0], 2),
            "5m": round(load_avg[1], 2),
            "15m": round(load_avg[2], 2)
        },
        "threads": threads,
        "processMemoryMB": process_memory,
        "active": True
    }

@app.api_route("/health", methods=["GET", "OPTIONS"])
async def get_health_route(
    request: Request,
    cors_response=Depends(cors_health_preflight)
):
    # Handle preflight CORS
    if request.method == "OPTIONS":
        return cors_response

    # Collect and return health stats
    health_data = await run_in_threadpool(collect_health_data)

    return JSONResponse(
        status_code=200,
        content=health_data,
        headers={
            "X-Server-ID": serverId,
            "X-Response-Time": str(round(time.time(), 2)),
            "Access-Control-Allow-Origin": "*",  # for GET response
        }
    )

@app.get("/alive")
async def health():
    return {"status": "alive"}

# ---------------- Keep-alive Thread ----------------
def keep_alive():
    while True:
        try:
            requests.get("https://buddycoderserver-d8iy.onrender.com/alive", timeout=5)
        except Exception:
            pass
        time.sleep(300)

threading.Thread(target=keep_alive, daemon=True).start()
