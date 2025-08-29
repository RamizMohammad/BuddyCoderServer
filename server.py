import threading
import time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import requests
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

# Add CORS middleware to allow cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# ---------------- Routes ----------------
PISTON_API_URL = "https://emkc.org/api/v2/piston/execute"

@app.post("/run")
async def run_code(request: Request):
    """
    Receives code via a POST request and executes it using the Piston API.
    This version removes the temporary SQLite database storage.
    """
    try:
        # Get JSON data from the request body
        data = await request.json()
        language = data.get("language", "python")
        version = data.get("version", "*")
        code = data.get("code", "")

        # Create the payload for the Piston API
        payload = {
            "language": language,
            "version": version,
            "files": [
                {"content": code}
            ]
        }
        
        # Make the API call directly to the Piston execution service
        response = requests.post(PISTON_API_URL, json=payload, timeout=10) # Added a timeout for safety
        
        # Check if the API call was successful
        response.raise_for_status()
        
        # Return the JSON response from the Piston API
        result = response.json()
        return JSONResponse(content=result)

    except requests.exceptions.RequestException as e:
        # Handle errors related to the Piston API call
        return JSONResponse(content={"error": f"Failed to connect to execution service: {e}"}, status_code=500)
    except Exception as e:
        # Handle other general errors
        return JSONResponse(content={"error": f"An unexpected error occurred: {e}"}, status_code=500)

@app.get("/health")
async def health():
    """
    Health check endpoint to ensure the service is running.
    """
    return {"status": "alive"}

# ---------------- Keep-alive Thread ----------------
def keep_alive():
    """
    A separate thread to ping the health endpoint periodically,
    preventing the application from sleeping on certain hosting platforms.
    """
    while True:
        try:
            requests.get("http://localhost:8000/health", timeout=5)
        except Exception:
            # Silently ignore connection errors
            pass
        time.sleep(300)  # Ping every 5 minutes (300 seconds)

# Start the keep-alive thread as a daemon thread
threading.Thread(target=keep_alive, daemon=True).start()

# To run the application, use `uvicorn main:app --reload`
