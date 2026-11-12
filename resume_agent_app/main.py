"""FastAPI server for the Resume Updating Agent (application under test).

Run:  uvicorn resume_agent_app.main:app --port 8000
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .agent import AgentError, model_info, run_agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("resume_agent.api")

MAX_MESSAGE_CHARS = 2000
MAX_RESUME_CHARS = 15000
MAX_UPLOAD_BYTES = 200 * 1024
ALLOWED_UPLOAD_SUFFIXES = {".txt", ".md"}
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Resume Updating Agent")


class Turn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    resume: str = ""
    message: str = ""
    history: list[Turn] = []


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "model": model_info()}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        return _error(400, "Unsupported file type. Please upload a .txt or .md resume.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return _error(400, "File is too large. Maximum size is 200 KB.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return _error(400, "File is not valid UTF-8 text.")
    if not text.strip():
        return _error(400, "The uploaded file is empty.")
    return {"resume": text}


@app.post("/api/chat")
def chat(req: ChatRequest):
    message = req.message.strip()
    if not req.resume.strip():
        return _error(400, "Please add your resume before requesting changes.")
    if not message:
        return _error(400, "Please enter an update request.")
    if len(message) > MAX_MESSAGE_CHARS:
        return _error(400, f"Request is too long (maximum {MAX_MESSAGE_CHARS} characters).")
    if len(req.resume) > MAX_RESUME_CHARS:
        return _error(400, f"Resume is too long (maximum {MAX_RESUME_CHARS} characters).")
    try:
        result = run_agent(req.resume, message, [t.model_dump() for t in req.history])
    except AgentError as exc:
        log.error("Agent failure: %s", exc)
        return _error(502, "The agent could not process this request. Please try again.")
    return {
        "action": result.action,
        "reply": result.reply,
        "updated_resume": result.updated_resume,
        "changed_sections": result.changed_sections,
        "model": result.model,
    }
