"""KaiClip Studio Móvil – mobile-first web app for user-authorized video clips.

Temporary files are private to unguessable job IDs. For single-instance deployments;
not designed for untrusted multi-tenant production without access control.
"""
from __future__ import annotations

import os
import importlib.util
import secrets
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from engine import VideoError, make_clips, zip_results

ROOT = Path(__file__).parent
DATA_DIR = Path(os.getenv("CLIP_DATA_DIR", str(Path(tempfile.gettempdir()) / "kaiclip_mobile")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
MAX_BYTES = int(os.getenv("MAX_UPLOAD_MB", "250")) * 1024 * 1024
TTL_SECONDS = int(os.getenv("CLIP_TTL_HOURS", "3")) * 3600
APP_PIN = os.getenv("APP_PIN", "").strip()

app = FastAPI(title="KaiClip Studio Móvil", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
_jobs: dict[str, dict] = {}
_lock = threading.RLock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kai-clip")
_STATES = {"queued", "processing", "done", "error"}


def check_pin(pin: str | None) -> None:
    if APP_PIN and not secrets.compare_digest((pin or "").strip(), APP_PIN):
        raise HTTPException(401, "PIN incorrecto.")


def purge_old() -> None:
    now = time.time()
    with _lock:
        expired = [jid for jid, job in _jobs.items() if job["state"] in ("done", "error") and now - job["created"] > TTL_SECONDS]
        for jid in expired:
            _jobs.pop(jid, None)
            shutil.rmtree(DATA_DIR / jid, ignore_errors=True)


def summary(job: dict) -> dict:
    return {k: job[k] for k in ("id", "state", "status", "created", "warnings", "clips")}


def process_job(jid: str, video: Path, clip_count: int, duration: int, framing: str,
                subtitles: bool, language: str) -> None:
    def status(message: str) -> None:
        with _lock:
            if jid in _jobs:
                _jobs[jid]["status"] = message

    try:
        with _lock:
            _jobs[jid]["state"] = "processing"
        dest = DATA_DIR / jid / "clips"
        model = "tiny.en" if language == "en" else "tiny"
        result = make_clips(video, dest, duration, clip_count, subtitles=subtitles,
                            language=language, model_size=model,
                            mode="Completo con fondo difuminado" if framing == "blur" else "Recorte vertical",
                            callback=status)
        zip_results(dest)
        public = []
        for i, c in enumerate(result["clips"], 1):
            public.append({
                "number": i,
                "name": c["name"],
                "duration": round(c["end"] - c["start"], 1),
                "start": round(c["start"], 1),
                "caption": c["caption"],
                "transcript": c["transcript"][:600],
                "has_subtitles": bool(c["srt"]),
            })
        with _lock:
            if jid in _jobs:
                _jobs[jid].update(state="done", status="¡Tus clips están listos!",
                                  clips=public, warnings=result.get("warnings", []))
    except Exception as exc:
        with _lock:
            if jid in _jobs:
                # No internals to end user, but a specific error is useful for invalid media.
                _jobs[jid].update(state="error", status=str(exc)[:480], clips=[])
    finally:
        video.unlink(missing_ok=True)


@app.get("/", response_class=HTMLResponse)
def homepage():
    return (ROOT / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/config")
def config():
    return {"max_upload_mb": MAX_BYTES // 1024 // 1024, "pin_required": bool(APP_PIN), "ttl_hours": TTL_SECONDS // 3600, "captions_available": bool(importlib.util.find_spec("faster_whisper"))}


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/jobs", status_code=202)
async def create_job(video: UploadFile = File(...), count: int = Form(4),
                     duration: int = Form(30), framing: str = Form("blur"),
                     subtitles: bool = Form(False), language: str = Form("en"),
                     authorized: bool = Form(False), x_app_pin: str | None = Header(default=None)):
    check_pin(x_app_pin)
    purge_old()
    if not authorized:
        raise HTTPException(400, "Confirma que tienes derecho a reutilizar el contenido.")
    if not 1 <= count <= 8 or not 10 <= duration <= 90 or framing not in ("crop", "blur") or language not in ("en", "es", "Auto"):
        raise HTTPException(400, "Opciones de recorte incorrectas.")
    extension = Path(video.filename or "").suffix.lower()
    if extension not in (".mp4", ".mov", ".mkv", ".webm", ".avi"):
        raise HTTPException(415, "Selecciona un archivo MP4, MOV, MKV, WEBM o AVI.")
    # Limit untrusted uploads and concurrent encoding jobs.
    with _lock:
        backlog = sum(j["state"] in ("queued", "processing") for j in _jobs.values())
        if backlog >= 2:
            raise HTTPException(429, "El servidor está ocupado. Inténtalo después.")
        jid = secrets.token_urlsafe(20)
        _jobs[jid] = dict(id=jid, state="queued", status="Recibiendo video…", created=time.time(), warnings=[], clips=[])
    workspace = DATA_DIR / jid
    workspace.mkdir(parents=True, exist_ok=True)
    source = workspace / f"original{extension}"
    size = 0
    try:
        with source.open("wb") as handle:
            while chunk := await video.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, f"El video supera el límite de {MAX_BYTES // (1024 * 1024)} MB.")
                handle.write(chunk)
        if size == 0:
            raise HTTPException(400, "Archivo vacío.")
    except Exception:
        with _lock:
            _jobs.pop(jid, None)
        shutil.rmtree(workspace, ignore_errors=True)
        raise
    finally:
        await video.close()
    with _lock:
        _jobs[jid]["status"] = "Video recibido, preparando análisis…"
    _executor.submit(process_job, jid, source, count, duration, framing, subtitles, language)
    return {"job_id": jid}


@app.get("/api/jobs/{jid}")
def get_job(jid: str, x_app_pin: str | None = Header(default=None)):
    check_pin(x_app_pin)
    purge_old()
    with _lock:
        job = _jobs.get(jid)
        if job is None:
            raise HTTPException(404, "El trabajo ha expirado o no existe.")
        return summary(job)


@app.get("/api/jobs/{jid}/files/{filename}")
def get_file(jid: str, filename: str, x_app_pin: str | None = Header(default=None)):
    # Since HTML video and anchors cannot attach auth headers, job-specific URLs
    # serve as bearer-capabilities. IDs are cryptographically unpredictable.
    purge_old()
    with _lock:
        job = _jobs.get(jid)
        if not job or job["state"] != "done":
            raise HTTPException(404, "Clip no disponible.")
        allowed = {c["name"] for c in job["clips"]}
        allowed.add("clips_tiktok.zip")
        if filename not in allowed:
            raise HTTPException(404, "Archivo no disponible.")
    file_path = DATA_DIR / jid / "clips" / filename
    if not file_path.is_file():
        raise HTTPException(404, "Archivo no disponible.")
    mimetype = "video/mp4" if filename.endswith(".mp4") else "application/zip"
    response = FileResponse(file_path, media_type=mimetype, filename=filename, content_disposition_type="inline" if mimetype == "video/mp4" else "attachment")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
