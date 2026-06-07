"""
main.py — VPS webhook receiver + data store for webapp
"""
import json
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI()

DATA_DIR = Path("/app/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
PLAN_FILE = DATA_DIR / "current_plan.json"

SHARED_SECRET = os.environ["VPS_SHARED_SECRET"]
ALLOWED_IP = os.environ.get("NUC_ALLOWED_IP", "")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://recepten.example.com"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def verify_secret(request: Request):
    secret = request.headers.get("X-Secret", "")
    if secret != SHARED_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    # Optional IP check
    if ALLOWED_IP:
        client_ip = request.headers.get("X-Real-IP") or request.client.host
        if client_ip != ALLOWED_IP:
            raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/api/push")
async def receive_plan(request: Request, _=Depends(verify_secret)):
    """Receive weekly plan from NUC and store it."""
    data = await request.json()
    PLAN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return {"status": "ok"}


@app.get("/api/plan")
async def get_plan():
    """Serve current plan to webapp."""
    if not PLAN_FILE.exists():
        raise HTTPException(status_code=404, detail="No plan found")
    return json.loads(PLAN_FILE.read_text())


@app.get("/api/health")
async def health():
    return {"status": "ok"}
