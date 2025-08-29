import tempfile
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pistonpy import PistonApp
from fastapi.middleware.cors import CORSMiddleware

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

        # ✅ Create a temporary file for the submitted code
        ext = get_extension(language)
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext, mode="w", encoding="utf-8") as tmp_file:
            tmp_file.write(code)
            tmp_filename = tmp_file.name

        try:
            # ✅ Pass the file path to piston
            result = piston.run(
                language=language,
                version=version,
                files=[tmp_filename]
            )
        finally:
            # Clean up file
            if os.path.exists(tmp_filename):
                os.remove(tmp_filename)

        print(result)
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
