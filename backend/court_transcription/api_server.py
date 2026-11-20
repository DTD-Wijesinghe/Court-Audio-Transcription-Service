from __future__ import annotations

import collections
import base64
import time
import json
import os
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel

from .batch_jobs import (
    build_proceeding_chunks_jsonl,
    build_translation_chunks_jsonl, 
    build_text_jsonl,
    build_transcript_audit_jsonl,
    build_transcript_repair_jsonl,
    build_transcription_jsonl,
    is_frequency_penalty_validation_error,
    submit_batch_job,
    wait_for_batch_job,
)
from .chunking import (
    SKIPPED_CHUNK_TRANSCRIPT,
    count_chunk_groups,
    filename_stem_from_uri,
    is_skipped_audio_uri,
    merge_chunk_transcripts,
)
from .config import (
    ACCURATE_MODEL,
    AWS_REGION,
    CHUNK_AUDIO_ON_UPLOAD,
    ENHANCE_AUDIO_ON_UPLOAD,
    ENABLE_REPETITION_REPAIR,
    ENABLE_TRANSCRIPT_COVERAGE_AUDIT,
    FAST_MODEL,
    FIREBASE_PROJECT_ID,
    INPUT_BUCKET,
    MAX_CONCURRENT_AUDIO_JOBS,
    MAX_JOBS_PER_COURT,         
    MAX_QUEUED_AUDIO_JOBS,   
    OUTPUT_BUCKET,
    S3_BUCKET,
    S3_NAMESPACE,
    REPETITION_REPAIR_MIN_REMOVED,
    TRANSCRIPTION_QUALITY,
)
from .gcs_utils import SUPPORTED_AUDIO_EXTENSIONS, download_and_parse_results, get_mime_type, upload_audio_file, upload_to_gcs
from .output_utils import build_header, remove_repetition_loops, save_results_locally, transcript_text_to_srt, write_txt
from .s3_utils import (
    get_s3_object_bytes,
    get_s3_object_text,
    key_from_storage_uri,
    list_s3_directory_entries,
    list_s3_prefix_objects,
    stream_s3_object_range,
    upload_bytes,
    upload_file_to_s3,
    upload_text,
)
from .errors import classify_exception, code_for_http_status, error_info, error_payload
from .temp_storage import (
    cleanup_job_workspace,
    cleanup_stale_workspaces,
    create_job_workspace,
    disk_metrics,
    has_capacity,
    safe_unlink,
)

BASE_DIR = Path(__file__).resolve().parents[1]
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"
UPLOADS_DIR = BASE_DIR / "uploads" / "api"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
LANGUAGE_NAMES = {"si": "Sinhala", "ta": "Tamil", "en": "English"}
COLOMBO_TIMEZONE = ZoneInfo("Asia/Colombo")
VALID_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
SAFE_CASE_SEGMENT = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,127}$")
SAFE_COURT_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
FIRESTORE_TIMEOUT_SECONDS = 10
audio_processing_slots = threading.BoundedSemaphore(MAX_CONCURRENT_AUDIO_JOBS)

_court_slots: dict[str, threading.BoundedSemaphore] = {}
_court_slots_lock = threading.Lock()

TRANSLATION_CHUNK_SIZES: dict[str, int] = {
    "si": 20_000,   
    "ta": 30_000,   
    "en": 50_000,   
}

def _now_colombo() -> datetime:
    return datetime.now(COLOMBO_TIMEZONE)

def _get_court_semaphore(court_slug: str) -> threading.BoundedSemaphore:
    with _court_slots_lock:
        if court_slug not in _court_slots:
            _court_slots[court_slug] = threading.BoundedSemaphore(MAX_JOBS_PER_COURT)
        return _court_slots[court_slug]

def _job_counts() -> dict[str, int]:
    with jobs_lock:
        all_jobs = list(jobs.values())
    phase1_active = sum(1 for j in all_jobs if j.get("status") in {
        "saving_to_s3", "enhancing_audio",
        "chunking_audio", "uploading_chunks_to_gcs",
        "submitted_to_vertex",
    })
    queued = sum(1 for j in all_jobs if j.get("status") == "queued")
    vertex = sum(1 for j in all_jobs if j.get("status") == "processing")
    per_court: dict[str, int] = {}
    for j in all_jobs:
        if j.get("status") not in {"completed", "failed"}:
            slug = j.get("courtSlug", "unknown")
            per_court[slug] = per_court.get(slug, 0) + 1
    return {
        "active": phase1_active,
        "queued": queued,
        "vertex": vertex,
        "total":  phase1_active + queued + vertex,
        "perCourt": per_court,
    }

app = FastAPI(
    title="IntelliScribe Court Audio Transcription API",
    description="REST API for court audio upload, transcription, editing, finalization, translation and download.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", "").strip()[:128] or uuid.uuid4().hex
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(StarletteHTTPException)
async def structured_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    code = code_for_http_status(exc.status_code)
    info = error_info(code)
    # Existing endpoint messages are safe and more specific than the catalog default.
    message = exc.detail if isinstance(exc.detail, str) else info.message
    payload = error_payload(info, request_id)
    payload["error"]["message"] = message
    payload["detail"] = message
    return JSONResponse(status_code=exc.status_code, content=payload, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def structured_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    info = error_info("REQ-0001")
    details = [
        {"field": ".".join(str(part) for part in error.get("loc", [])[1:]), "message": error.get("msg", "Invalid value")}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content=error_payload(info, request_id, details=details))


@app.exception_handler(Exception)
async def structured_unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    info = classify_exception(exc)
    _log_event("Error", "API Server", f"{info.code} request failed", f"requestId={request_id} · {type(exc).__name__}: {str(exc)[:160]}")
    return JSONResponse(status_code=info.http_status, content=error_payload(info, request_id))

# ── Add these imports at the top of the file if not already there ──
# import time   ← already used in batch_jobs.py but add to api_server.py too
from .config import PROJECT_ID, LOCATION   # ← add these to existing import


@app.on_event("startup")
def _startup() -> None:
    """Start workers without allowing an external service to block API startup."""
    print("\n" + "=" * 60)
    print("  IntelliScribe Court Audio Transcription API")
    print("  Swagger UI: http://localhost:8000/docs")
    print("  ReDoc:      http://localhost:8000/redoc")
    print("  OpenAPI:    http://localhost:8000/openapi.json")
    print("=" * 60 + "\n")
    threading.Thread(target=_cleanup_stale_temp_storage, daemon=True).start()
    threading.Thread(target=_recover_jobs_from_s3, daemon=True).start()
    threading.Thread(target=_poll_vertex_jobs, daemon=True).start()
    threading.Thread(target=_load_token_prices_from_s3, daemon=True).start()

def _load_token_prices_from_s3() -> None:
    global _token_prices
    try:
        if S3_BUCKET:
            txt = get_s3_object_text(S3_BUCKET, _TOKEN_PRICES_KEY, region=AWS_REGION)
            if txt:
                saved = json.loads(txt)
                merged = dict(_DEFAULT_TOKEN_PRICES)
                for key in merged:
                    if key in saved and isinstance(saved[key], (int, float)):
                        merged[key] = float(saved[key])
                _token_prices = merged
                print("[startup] Token prices loaded from S3")
    except Exception as exc:
        print(f"[startup] Could not load token prices from S3: {exc}")

def _cleanup_stale_temp_storage() -> None:
    try:
        with jobs_lock:
            active = [job_id for job_id, job in jobs.items() if job.get("status") not in {"completed", "failed"}]
        result = cleanup_stale_workspaces(active)
        if result["removed"]:
            _log_event(
                "Info", "Temporary Storage", "Stale workspaces cleaned",
                f"removed={result['removed']} · reclaimedBytes={result['reclaimedBytes']}",
            )
    except Exception as exc:
        _log_event("Warning", "Temporary Storage", "Stale workspace cleanup failed", str(exc)[:160])


def _recover_jobs_from_s3() -> None:
    """Recover jobs asynchronously; AWS outages must not make the whole API unavailable."""
    if S3_BUCKET:
        try:
            recovered = 0
            for obj in _list_s3_prefix_objects("jobs/"):
                try:
                    txt = get_s3_object_text(S3_BUCKET, obj["Key"], region=AWS_REGION)
                    if txt:
                        job = json.loads(txt)
                        if job.get("status") == "processing" and job.get("id"):
                            with jobs_lock:
                                jobs[job["id"]] = job
                            recovered += 1
                except Exception as exc:
                    public_error = classify_exception(exc)
                    _log_event("Warning", "S3", f"{public_error.code} job recovery item failed", str(exc)[:160])
            if recovered:
                print(f"[startup] Recovered {recovered} in-progress job(s) from S3")
                _log_event("Info", "Poller", f"Server started - recovered {recovered} in-progress job(s) from S3")
            else:
                _log_event("Info", "Poller", "Server started - no in-progress jobs to recover")
            
        except Exception as exc:
            public_error = classify_exception(exc)
            print(f"[startup] {public_error.code}: {public_error.message}")
            _log_event("Error", "S3", f"{public_error.code} job recovery failed", str(exc)[:160])

jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.Lock()

# Operational event buffer 
_event_buffer: collections.deque = collections.deque(maxlen=1000)

_TOKEN_PRICES_KEY = "config/token_prices.json"
_DEFAULT_TOKEN_PRICES: dict[str, float] = {
    "audioInput":     0.50,
    "textInput":      0.15,
    "textOutput":     0.60,
    "thinkingTokens": 3.50,
    "cachedTokens":   0.04,
}
_token_prices: dict[str, float] = dict(_DEFAULT_TOKEN_PRICES)

def _log_event(
    level: str,
    service: str,
    event: str,
    detail: str = "",
    job_id: str = "",
    case_number: str = "",
    court: str = "",
    uploaded_by: str = "",
    duration_seconds: float = 0.0,
) -> None:
    """Append a structured operational event to the in-memory buffer."""
    try:
        _event_buffer.append({
            "time":            _now_colombo().strftime("%Y-%m-%d %H:%M:%S"),
            "level":           level,
            "service":         service,
            "event":           event,
            "detail":          detail,
            "jobId":           job_id,
            "caseNumber":      case_number,
            "court":           court,
            "uploadedBy":      uploaded_by,
            "durationSeconds": round(duration_seconds, 1),
        })
    except Exception:
        pass

@dataclass(frozen=True)
class AuthenticatedUser:
    uid: str
    project_id: str
    court: str
    court_slug: str
    display_name: str
    username: str
    email: str
    token: str


class EditedTranscriptRequest(BaseModel):
    content: str


class PostProcessRequest(BaseModel):
    translation_languages: list[str] = []
    include_summary: bool = False


def _finalize_vertex_job(job_id: str, job_data: dict) -> None:
    """
    Phase 2: called by poller when Vertex reports JOB_STATE_SUCCEEDED.
    Downloads results from GCS, optionally runs coverage audit, saves to S3.
    """
    workspace: Path | None = None
    try:
        workspace = create_job_workspace(f"{job_id}-finalize")
        from google import genai
        from google.genai.types import HttpOptions

        finalize_start  = time.time()
        audio_uris      = job_data["audioUris"]
        output_prefix   = job_data["outputGcsPrefix"]
        court           = job_data["court"]
        court_slug      = job_data["courtSlug"]
        case_folder     = job_data["caseFolder"]
        case_number     = job_data["caseNumber"]
        hearing_date    = job_data["hearingDate"]
        uploaded_by     = job_data["uploadedBy"]
        uploader_uid    = job_data["uploaderUid"]
        stored_name     = job_data["storedAudioName"]
        gcs_uri         = job_data["gcsUri"]
        s3_audio_uri    = job_data.get("s3AudioUri", "")
        s3_audio_uris   = job_data.get("s3AudioUris", [s3_audio_uri] if s3_audio_uri else [])
        quality         = job_data.get("transcriptionQuality", _selected_transcription_quality())
        model           = job_data.get("transcriptionModel", _selected_transcription_model())
        timestamp       = job_data.get("timestamp", _now_colombo().strftime("%Y%m%d_%H%M%S"))

        _set_job(job_id, status="downloading_results", message="Downloading transcript from Google Cloud")
        gcs_start = time.time()
        transcripts = download_and_parse_results(output_prefix)
        if not transcripts:
            raise RuntimeError("No transcription results returned from GCS.")
        _log_event(
            "Info", "GCS",
            "Transcription results downloaded from GCS",
            f"{len(transcripts)} result(s) parsed",
            job_id=job_id, case_number=case_number,
            court=court, uploaded_by=uploaded_by,
            duration_seconds=round(time.time() - gcs_start, 1),
        )

        transcription_usage_entries = {k: v for k, v in transcripts.items() if k.endswith("__usage")}
        transcripts = {k: v for k, v in transcripts.items() if not k.endswith("__usage")}
        transcription_usage = _sum_usage_entries(transcription_usage_entries)
        repair_usage = {"audioInput": 0, "textInput": 0, "textOutput": 0, "thinkingTokens": 0, "cachedTokens": 0, "total": 0}
        audit_usage = {"audioInput": 0, "textInput": 0, "textOutput": 0, "thinkingTokens": 0, "cachedTokens": 0, "total": 0}

        transcripts = _add_skipped_chunk_markers(audio_uris, transcripts)

        # Log the batch keys that will be matched to uploaded audio files/chunks.
        print(f"[transcript] Available transcript keys: {list(transcripts.keys())}")
        print(f"[transcript] Audio URI count: {len(audio_uris)}")

        if ENABLE_REPETITION_REPAIR:
            repair_items: list[tuple[int, str, str]] = []
            for idx, uri in enumerate(audio_uris):
                filename_stem = uri.split("/")[-1].rsplit(".", 1)[0]
                transcript_key = f"{idx}|{filename_stem}|transcript"
                draft = (transcripts.get(transcript_key, "") or "").strip()
                if not draft:
                    continue
                _, removed_count = remove_repetition_loops(draft)
                if removed_count >= REPETITION_REPAIR_MIN_REMOVED:
                    repair_items.append((idx, uri, draft))

            if repair_items:
                _set_job(
                    job_id,
                    status="repairing_transcript",
                    message=f"Repairing {len(repair_items)} repeated transcript(s)",
                )
                repair_start = time.time()
                _log_event(
                    "Warning", "API Server",
                    f"Repetition repair triggered - {len(repair_items)} transcript(s)",
                    f"repair job submitting · model: {model}",
                    job_id=job_id, case_number=case_number,
                    court=court, uploaded_by=uploaded_by,
                )
                repair_jsonl_name = f"api_repair_{timestamp}_{job_id}.jsonl"
                repair_jsonl_local = str(workspace / "batch" / repair_jsonl_name)
                repair_jsonl_gcs_blob = f"batch_inputs/{repair_jsonl_name}"
                repair_entries = build_transcript_repair_jsonl(repair_items, repair_jsonl_local)
                if repair_entries:
                    try:
                        repair_gcs_uri = upload_to_gcs(repair_jsonl_local, INPUT_BUCKET, repair_jsonl_gcs_blob)
                    finally:
                        safe_unlink(repair_jsonl_local)
                    repair_prefix = f"gs://{OUTPUT_BUCKET}/batch_outputs/{timestamp}_{job_id}_repair/"
                    repair_vertex_job, repair_client = submit_batch_job(
                        repair_gcs_uri, repair_prefix, model_override=model
                    )
                    repair_ok, _ = wait_for_batch_job(repair_vertex_job, repair_client)
                    _log_event(
                        "Info", "API Server",
                        f"Repetition repair completed",
                        f"duration: {round(time.time() - repair_start, 1)}s",
                        job_id=job_id, case_number=case_number,
                        court=court, uploaded_by=uploaded_by,
                        duration_seconds=round(time.time() - repair_start, 1),
                    )
                    if repair_ok:
                        repaired_raw = download_and_parse_results(repair_prefix)
                        repair_usage_entries = {k: v for k, v in repaired_raw.items() if k.endswith("__usage")}
                        repair_usage = _sum_usage_entries(repair_usage_entries)
                        repaired = {
                            k: v for k, v in repaired_raw.items()
                            if k.endswith("|transcript") and str(v or "").strip()
                        }
                        if repaired:
                            transcripts.update(repaired)
                            print(f"[repair] Repaired {len(repaired)} repeated transcript(s)")

        # Coverage audit (same as before, but now async)
        if ENABLE_TRANSCRIPT_COVERAGE_AUDIT:
            _set_job(job_id, status="verifying", message="Running coverage audit")
        audit_jsonl_name = f"api_audit_{timestamp}_{job_id}.jsonl"
        audit_jsonl_local = str(workspace / "batch" / audit_jsonl_name)
        audit_jsonl_gcs_blob = f"batch_inputs/{audit_jsonl_name}"
        audit_entries = (
            build_transcript_audit_jsonl(audio_uris, transcripts, audit_jsonl_local, quality=quality)
            if ENABLE_TRANSCRIPT_COVERAGE_AUDIT
            else 0
        )
        coverage_audited = False
        if ENABLE_TRANSCRIPT_COVERAGE_AUDIT and audit_entries:
            try:
                audit_gcs_uri = upload_to_gcs(audit_jsonl_local, INPUT_BUCKET, audit_jsonl_gcs_blob)
            finally:
                safe_unlink(audit_jsonl_local)
            audit_prefix = f"gs://{OUTPUT_BUCKET}/batch_outputs/{timestamp}_{job_id}_audit/"
            audit_vertex_job, audit_client = submit_batch_job(
                audit_gcs_uri, audit_prefix, model_override=model
            )
            # The audit job is small — acceptable to wait here (few seconds to minutes)
            audit_ok, _ = wait_for_batch_job(audit_vertex_job, audit_client)
            if audit_ok:
                audited_raw = download_and_parse_results(audit_prefix)
                audit_usage_entries = {k: v for k, v in audited_raw.items() if k.endswith("__usage")}
                audit_usage = _sum_usage_entries(audit_usage_entries)
                audited = {
                    k: v for k, v in audited_raw.items()
                    if k.endswith("|transcript") and str(v or "").strip()
                }
                if audited:
                    transcripts.update(audited)
                    coverage_audited = True

        _set_job(job_id, status="merging_transcript", message="Merging transcript chunks")
        parent_audio_uris, transcripts = merge_chunk_transcripts(audio_uris, transcripts)
        if len(parent_audio_uris) != len(audio_uris):
            print(f"[merge] Merged chunk transcripts into {len(parent_audio_uris)} source transcript(s)")
        audio_uris = parent_audio_uris
        if audio_uris:
            gcs_uri = audio_uris[0]

        # Merge multiple transcripts into one
        if len(audio_uris) > 1:
            _set_job(job_id, status="merging_transcript", message="Combining uploaded audio transcripts")
            merged_text = ""

            # Try indexed key format first: "0|stem|transcript"
            for idx, uri in enumerate(audio_uris):
                filename_stem = uri.split("/")[-1].rsplit(".", 1)[0]
                key = f"{idx}|{filename_stem}|transcript"
                text = _find_transcript_text(transcripts, idx, uri)
                if text:
                    print(f"[merge] Found transcript for key: {key}")
                    if merged_text:
                        merged_text += "\n\n"
                    merged_text += text

            # Fallback — if indexed keys didn't work, merge ALL transcript values
            if not merged_text:
                print("[merge] Indexed keys not found - merging all transcript values")
                all_texts = [
                    v.strip() for k, v in transcripts.items()
                    if k.endswith("|transcript") and v and v.strip()
                ]
                merged_text = "\n\n".join(all_texts)

            if merged_text:
                print(f"[merge] Merged {len(audio_uris)} transcripts successfully")
                first_stem = audio_uris[0].split("/")[-1].rsplit(".", 1)[0]
                transcripts = {f"0|{first_stem}|transcript": merged_text}
                audio_uris = [audio_uris[0]]
            else:
                print("[merge] WARNING: No text found to merge!")

        _set_job(job_id, status="saving_transcript_to_s3", message="Saving transcript to S3")
        folder_name, s3_files = save_results_locally(
            audio_uris, transcripts, [], False,
            output_dir=str(TRANSCRIPTS_DIR),
            result_date=hearing_date,
            court=court_slug,
            original_filename=stored_name,
            case_folder=case_folder,
        )

        token_usage = {
            "transcription": transcription_usage,
            "repair": repair_usage,
            "audit": audit_usage,
            "summary": {"audioInput": 0, "textInput": 0, "textOutput": 0, "thinkingTokens": 0, "cachedTokens": 0, "total": 0},
            "translations": {},
        }

        item = _write_metadata(
            folder_name,
            case_number=case_number,
            hearing_date=hearing_date,
            court=court,
            court_slug=court_slug,
            uploaded_by=uploaded_by,
            original_file_name=stored_name,
            gcs_uri=gcs_uri,
            translate_langs=[],
            include_summary=False,
            local_audio_path="",
            s3_files=s3_files,
            s3_audio_uri=s3_audio_uri,
            s3_audio_uris=s3_audio_uris,
            coverage_audited=coverage_audited,
            audio_duration_seconds=job_data.get("audioDurationSeconds", 0.0),
            token_usage=token_usage,
        )

        _thinking = transcription_usage.get('thinkingTokens', 0)
        _cached   = transcription_usage.get('cachedTokens', 0)
        _detail = (
            f"{transcription_usage.get('total', 0):,} tokens · "
            f"audio {transcription_usage.get('audioInput', 0):,} · "
            f"txt in {transcription_usage.get('textInput', 0):,} · "
            f"txt out {transcription_usage.get('textOutput', 0):,}"
            + (f" · thinking {_thinking:,}" if _thinking > 0 else "")
            + (f" · cached {_cached:,} (10% rate)" if _cached > 0 else "")
        )
        _log_event(
            "Success", "Vertex AI",
            "Transcription completed",
            _detail,
            job_id=job_id, case_number=case_number,
            court=court, uploaded_by=uploaded_by,
            duration_seconds=round(time.time() - finalize_start, 1),
        )
        _set_job(
            job_id,
            status="completed",
            message="Transcript is ready",
            transcript=item,
            savedFiles=s3_files,
            court=court,
            courtSlug=court_slug,
            uploaderUid=uploader_uid,
        )

    except Exception as exc:
        public_error = classify_exception(exc)
        _set_job(job_id, status="failed", message=public_error.message, errorCode=public_error.code, error={
            "code": public_error.code, "message": public_error.message,
            "category": public_error.category, "retryable": public_error.retryable,
        })
        _log_event(
            "Error", "API Server",
            "Transcription finalization failed",
            str(exc)[:200],
            job_id=job_id,
            case_number=job_data.get("caseNumber", ""),
            court=job_data.get("court", ""),
            uploaded_by=job_data.get("uploadedBy", ""),
        )
    finally:
        if workspace is not None:
            try:
                cleanup_job_workspace(workspace)
            except Exception as cleanup_exc:
                _log_event("Warning", "Temporary Storage", "Finalization workspace cleanup failed", str(cleanup_exc)[:160], job_id=job_id)

def _poll_vertex_jobs() -> None:
    """
    Runs forever in a background daemon thread.
    Every 60 seconds, checks all jobs with status='processing'.
    When Vertex AI reports success → calls _finalize_vertex_job.
    """
    from google import genai
    from google.genai.types import HttpOptions, JobState

    print("[poller] Vertex AI job poller started")
    while True:
        time.sleep(60)
        try:
            with jobs_lock:
                pending = [
                    (jid, dict(j))
                    for jid, j in jobs.items()
                    if j.get("status") == "processing" and j.get("vertexJobName")
                ]
        except Exception:
            continue

        if not pending:
            continue

        try:
            client = genai.Client(
                vertexai=True,
                project=PROJECT_ID,
                location=LOCATION,
                http_options=HttpOptions(api_version="v1beta1"),
            )
        except Exception:
            continue

        for job_id, job_data in pending:
            try:
                vertex_job = client.batches.get(name=job_data["vertexJobName"])
                state_name = vertex_job.state.name if hasattr(vertex_job.state, "name") else str(vertex_job.state)

                if state_name == "JOB_STATE_SUCCEEDED":
                    print(f"[poller] Job {job_id} succeeded - finalizing")
                    # Run finalization in its own thread so poller keeps going
                    _log_event(
                        "Info", "Poller",
                        "Vertex AI job succeeded - starting finalization",
                        f"job: {job_data.get('vertexJobName', '')[:60]}",
                        job_id=job_id,
                        case_number=job_data.get("caseNumber", ""),
                        court=job_data.get("court", ""),
                        uploaded_by=job_data.get("uploadedBy", ""),
                    )
                    threading.Thread(
                        target=_finalize_vertex_job,
                        args=(job_id, job_data),
                        daemon=True,
                    ).start()

                elif state_name in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED"):
                    error_msg = str(getattr(vertex_job, 'error', '') or '')
                    _set_job(
                        job_id,
                        status="failed",
                        message=f"Vertex AI job {state_name}: {error_msg}",
                    )
                    _log_event(
                        "Error", "Vertex AI",
                        f"Batch job {state_name}",
                        error_msg[:120] if error_msg else "No error detail available",
                        job_id=job_id,
                        case_number=job_data.get("caseNumber", ""),
                        court=job_data.get("court", ""),
                        uploaded_by=job_data.get("uploadedBy", ""),
                    )
            except Exception as e:
                err_str = str(e)
                print(f"[poller] Error checking job {job_id}: {e}")
                if "NOT_FOUND" in err_str or "404" in err_str:
                    _set_job(
                        job_id,
                        status="failed",
                        message="Vertex AI batch job no longer exists (expired or deleted)",
                    )
                    _log_event(
                        "Error", "Poller",
                        "Vertex AI batch job not found - marked as failed",
                        f"job: {job_data.get('vertexJobName', '')[:80]}",
                        job_id=job_id,
                        case_number=job_data.get("caseNumber", ""),
                        court=job_data.get("court", ""),
                        uploaded_by=job_data.get("uploadedBy", ""),
                    )

def _split_transcript_for_proceeding(
    text: str,
    max_chars: int = 30_000,
    overlap_lines: int = 10,
) -> list[str]:
    """
    Split a long transcript into smaller overlapping pieces.
    Returns a single-element list if no splitting is needed.
    """
    if len(text) <= max_chars:
        return [text]

    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0

    for line in lines:
        if current_size + len(line) > max_chars and current:
            chunks.append("".join(current))
            tail = current[-overlap_lines:]
            current = list(tail)
            current_size = sum(len(l) for l in current)
        current.append(line)
        current_size += len(line)

    if current:
        chunks.append("".join(current))

    return chunks

def _today() -> str:
    return _now_colombo().strftime("%Y-%m-%d")


def _selected_transcription_quality() -> str:
    quality = (TRANSCRIPTION_QUALITY or "accurate").strip().lower()
    return quality if quality in {"fast", "accurate"} else "accurate"


def _selected_transcription_model() -> str:
    return ACCURATE_MODEL if _selected_transcription_quality() == "accurate" else FAST_MODEL

def _empty_token_usage() -> dict[str, Any]:
    slot = {"audioInput": 0, "textInput": 0, "textOutput": 0, "thinkingTokens": 0, "cachedTokens": 0, "total": 0}
    return {
        "transcription": dict(slot),
        "repair": dict(slot),
        "audit": dict(slot),
        "summary": dict(slot),
        "translations": {},
    }


def _sum_usage_entries(usage_entries: dict[str, dict]) -> dict[str, Any]:
    return {
        "audioInput": sum(u.get("audioInput", 0) for u in usage_entries.values()),
        "textInput": sum(u.get("textInput", 0) for u in usage_entries.values()),
        "textOutput": sum(u.get("textOutput", 0) for u in usage_entries.values()),
        "thinkingTokens": sum(u.get("thinkingTokens", 0) for u in usage_entries.values()), 
        "cachedTokens":   sum(u.get("cachedTokens", 0)   for u in usage_entries.values()), 
        "total": sum(u.get("total", 0) for u in usage_entries.values()),
    }


def _add_usage(a: dict, b: dict) -> dict[str, Any]:
    return {
        "audioInput": a.get("audioInput", 0) + b.get("audioInput", 0),
        "textInput": a.get("textInput", 0) + b.get("textInput", 0),
        "textOutput": a.get("textOutput", 0) + b.get("textOutput", 0),
        "thinkingTokens": a.get("thinkingTokens", 0) + b.get("thinkingTokens", 0),  
        "cachedTokens":   a.get("cachedTokens", 0)   + b.get("cachedTokens", 0),  
        "total": a.get("total", 0) + b.get("total", 0),
    }

def _add_skipped_chunk_markers(audio_uris: list[str], transcripts: dict[str, str]) -> dict[str, str]:
    updated = dict(transcripts)
    for idx, uri in enumerate(audio_uris):
        if not is_skipped_audio_uri(uri):
            continue
        stem = filename_stem_from_uri(uri)
        updated.setdefault(f"{idx}|{stem}|transcript", SKIPPED_CHUNK_TRANSCRIPT)
    return updated


def _find_transcript_text(transcripts: dict[str, str], idx: int, uri: str) -> str:
    filename_stem = uri.split("/")[-1].rsplit(".", 1)[0]
    exact_key = f"{idx}|{filename_stem}|transcript"
    exact_text = str(transcripts.get(exact_key, "") or "").strip()
    if exact_text:
        return exact_text

    suffix = f"|{filename_stem}|transcript"
    matches = [
        str(value or "").strip()
        for key, value in transcripts.items()
        if str(key).endswith(suffix) and str(value or "").strip()
    ]
    if len(matches) == 1:
        return matches[0]

    return ""


def _validate_date(date_value: str | None) -> str:
    date_value = (date_value or _today()).strip()
    if not VALID_DATE.match(date_value):
        raise HTTPException(status_code=400, detail="Date must use YYYY-MM-DD format.")
    return date_value


def _parse_languages(raw: str | None) -> list[str]:
    selected: list[str] = []
    for part in (raw or "").replace(" ", "").split(","):
        if not part:
            continue
        if part not in LANGUAGE_NAMES:
            raise HTTPException(status_code=400, detail=f"Unsupported translation language: {part}")
        if part not in selected:
            selected.append(part)
    return selected


def _safe_filename(name: str, fallback: str) -> str:
    raw = (name or fallback or "audio").strip().strip('"').replace("\\", "/").split("/")[-1]
    raw = re.sub(r"[^A-Za-z0-9._()\[\] -]+", "_", raw).strip(" .")
    return raw or "audio"


def _slugify_court_room(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug or not SAFE_COURT_SEGMENT.match(slug):
        raise HTTPException(status_code=403, detail="Authenticated user is not assigned to a valid court room.")
    return slug


def _sanitize_case_number(case_number: str) -> str:
    normalized = re.sub(r"[^A-Z0-9_-]+", "-", (case_number or "").strip().upper())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-_")
    if not normalized or not SAFE_CASE_SEGMENT.match(normalized):
        raise HTTPException(status_code=400, detail="Case number contains unsupported characters.")
    return normalized


def _storage_prefix(court_slug: str, hearing_date: str, case_folder: str) -> str:
    return f"{S3_NAMESPACE}/{court_slug}/{hearing_date}/{case_folder}"


def _storage_prefix_for_folder(folder: Path) -> str:
    court_slug, hearing_date, case_folder = _folder_scope(folder)
    return _storage_prefix(court_slug, hearing_date, case_folder)


def _folder_scope(folder: Path) -> tuple[str, str, str]:
    return folder.parent.parent.name, folder.parent.name, folder.name


def _decode_token_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Invalid authentication token.")
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        data = base64.urlsafe_b64decode((payload + padding).encode("ascii")).decode("utf-8")
        parsed = json.loads(data)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid authentication token.") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=401, detail="Invalid authentication token.")
    return parsed


def _firestore_value(value: dict[str, Any]) -> Any:
    if "stringValue" in value:
        return value["stringValue"]
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "nullValue" in value:
        return None
    if "arrayValue" in value:
        return [_firestore_value(item) for item in value.get("arrayValue", {}).get("values", [])]
    if "mapValue" in value:
        return {k: _firestore_value(v) for k, v in value.get("mapValue", {}).get("fields", {}).items()}
    return None


def _fetch_firestore_user(project_id: str, uid: str, token: str) -> dict[str, Any]:
    url = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents/users/{uid}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=FIRESTORE_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise HTTPException(status_code=401, detail="Authentication token is not authorized for this user profile.") from exc
        if exc.code == 404:
            raise HTTPException(status_code=403, detail="Authenticated user profile was not found.") from exc
        raise HTTPException(status_code=502, detail="Could not load the authenticated user profile.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not load the authenticated user profile.") from exc

    fields = payload.get("fields", {})
    return {key: _firestore_value(value) for key, value in fields.items()}


def _require_authenticated_user(authorization: str | None = Header(default=None)) -> AuthenticatedUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication is required.")

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authentication is required.")

    unverified = _decode_token_payload(token)
    token_project_id = str(unverified.get("aud") or "").strip()
    expected_project_id = FIREBASE_PROJECT_ID or token_project_id
    if not expected_project_id:
        raise HTTPException(status_code=500, detail="Firebase project id is not configured.")
    if FIREBASE_PROJECT_ID and token_project_id and token_project_id != FIREBASE_PROJECT_ID:
        raise HTTPException(status_code=401, detail="Authentication token belongs to a different Firebase project.")

    try:
        claims = google_id_token.verify_firebase_token(
            token,
            google_requests.Request(),
            audience=expected_project_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Authentication token could not be verified.") from exc

    uid = str(claims.get("uid") or claims.get("user_id") or claims.get("sub") or "").strip()
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication token is missing the user id.")

    profile = _fetch_firestore_user(expected_project_id, uid, token)
    court = str(profile.get("court") or "").strip()
    court_slug = _slugify_court_room(court)
    return AuthenticatedUser(
        uid=uid,
        project_id=expected_project_id,
        court=court,
        court_slug=court_slug,
        display_name=str(profile.get("displayName") or "").strip(),
        username=str(profile.get("username") or "").strip(),
        email=str(profile.get("email") or "").strip(),
        token=token,
    )


def _ensure_storage_key_in_court(key: str, court_slug: str) -> str:
    normalized = (key or "").lstrip("/")
    allowed_prefix = f"{S3_NAMESPACE}/{court_slug}/"
    if not normalized.startswith(allowed_prefix):
        raise HTTPException(status_code=403, detail="Requested file does not belong to the authenticated user's court room.")
    return normalized


def _set_job(job_id: str, **updates: Any) -> None:
    with jobs_lock:
        current = jobs.setdefault(job_id, {})
        current.update(updates)
        current["updated_at"] = _now_colombo().isoformat(timespec="seconds")
    # Also persist job to S3 for durability across restarts
    try:
        if S3_BUCKET:
            key = f"jobs/{job_id}.json"
            upload_text(S3_BUCKET, key, json.dumps(current, ensure_ascii=False, indent=2), content_type="application/json", region=AWS_REGION)
    except Exception:
        pass


def _get_job(job_id: str) -> dict[str, Any]:
    with jobs_lock:
        job = jobs.get(job_id)
        if job:
            return dict(job)
    # Job not in memory; try to retrieve from S3
    try:
        if S3_BUCKET:
            key = f"jobs/{job_id}.json"
            txt = get_s3_object_text(S3_BUCKET, key, region=AWS_REGION)
            if txt:
                with jobs_lock:
                    job = json.loads(txt)
                    jobs[job_id] = job
                    return dict(job)
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="Upload job not found.")


def _get_authorized_job(job_id: str, current_user: AuthenticatedUser) -> dict[str, Any]:
    job = _get_job(job_id)
    job_court_slug = str(job.get("courtSlug") or "").strip()
    if not job_court_slug:
        raise HTTPException(status_code=404, detail="Upload job not found.")
    if job_court_slug != current_user.court_slug:
        raise HTTPException(status_code=403, detail="Upload job does not belong to the authenticated user's court room.")
    return job


def _encode_id(date_value: str, folder_name: str) -> str:
    rel = f"{date_value}/{folder_name}"
    return base64.urlsafe_b64encode(rel.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_id(item_id: str) -> str:
    padding = "=" * (-len(item_id) % 4)
    try:
        rel = base64.urlsafe_b64decode((item_id + padding).encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid transcript id.") from exc
    if not re.match(r"^\d{4}-\d{2}-\d{2}/[A-Za-z0-9._-]+$", rel):
        raise HTTPException(status_code=400, detail="Invalid transcript id.")
    return rel


def _resolve_item_dir(item_id: str, court_slug: str) -> Path:
    rel = _decode_id(item_id)
    base = TRANSCRIPTS_DIR.resolve()
    item_dir = (TRANSCRIPTS_DIR / court_slug / rel).resolve()
    try:
        item_dir.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid transcript path.") from exc
    # Don't require local existence — metadata and files are stored in S3
    return item_dir


def _read_text(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _strip_saved_text(text: str) -> str:
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n")
    lines = normalized.splitlines()
    if len(lines) >= 4 and set(lines[1].strip()) == {"="}:
        for idx, line in enumerate(lines[2:], start=2):
            if idx > 2 and line.strip() and set(line.strip()) == {"="}:
                lines = lines[idx + 1:]
                if lines and not lines[0].strip():
                    lines = lines[1:]
                break
    while lines and lines[-1].strip() == "END":
        lines.pop()
    while lines and (not lines[-1].strip() or set(lines[-1].strip()) == {"="}):
        lines.pop()
    return "\n".join(lines).strip()


def _read_body_text(path: Path | None) -> str:
    return _strip_saved_text(_read_text(path))


def _final_transcript_path(folder: Path) -> Path:
    return folder / "final_transcript.txt"


def _save_metadata_file(folder: Path, metadata: dict[str, Any]) -> None:
    # S3-only: upload metadata.json to S3 without local write
    try:
        if S3_BUCKET:
            key = f"{_storage_prefix_for_folder(folder)}/metadata.json"
            upload_text(S3_BUCKET, key, json.dumps(metadata, ensure_ascii=False, indent=2), content_type="application/json", region=AWS_REGION)
    except Exception:
        pass


def _update_metadata(folder: Path, **updates: Any) -> dict[str, Any]:
    metadata = _load_metadata(folder)
    metadata.update(updates)
    _save_metadata_file(folder, metadata)
    return metadata

def _main_transcript_path(folder: Path) -> Path | None:
    preferred = [folder / "transcript.txt", folder / "transcript2.txt"]
    for candidate in preferred:
        if candidate.exists():
            return candidate

    excluded_prefixes = ("summary", "translation_", "edited")
    for candidate in sorted(folder.glob("*.txt"), key=lambda p: p.name.lower()):
        name = candidate.name.lower()
        if not name.startswith(excluded_prefixes):
            return candidate
    return None


def _extract_source_file(text: str) -> str:
    for line in text.splitlines()[:12]:
        if line.lower().startswith("source file") and ":" in line:
            return line.split(":", 1)[1].strip()
    return ""


def _load_metadata(folder: Path) -> dict[str, Any]:
    # S3-only: read metadata.json from S3
    try:
        if S3_BUCKET:
            key = f"{_storage_prefix_for_folder(folder)}/metadata.json"
            txt = get_s3_object_text(S3_BUCKET, key, region=AWS_REGION)
            if txt:
                return json.loads(txt)
    except Exception:
        pass
    return {}


def _file_record(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "size": stat.st_size,
        "updatedAt": datetime.fromtimestamp(stat.st_mtime, tz=COLOMBO_TIMEZONE).isoformat(timespec="seconds"),
    }


def _list_s3_prefix_objects(prefix: str) -> list[dict[str, Any]]:
    if not S3_BUCKET:
        return []
    try:
        return list_s3_prefix_objects(S3_BUCKET, prefix, region=AWS_REGION)
    except Exception:
        return []


def _list_s3_directory_entries(prefix: str) -> list[dict[str, Any]]:
    if not S3_BUCKET:
        return []
    try:
        return list_s3_directory_entries(S3_BUCKET, prefix, region=AWS_REGION)
    except Exception:
        return []


def _s3_record_from_object(obj: dict[str, Any], prefix: str) -> dict[str, Any]:
    key = str(obj.get("Key") or "")
    name = key[len(prefix):].lstrip("/") if key.startswith(prefix) else key.rsplit("/", 1)[-1]
    updated = obj.get("LastModified")
    if hasattr(updated, "isoformat"):
        updated_at = updated.isoformat(timespec="seconds")
    elif updated:
        updated_at = str(updated)
    else:
        updated_at = ""
    return {
        "name": name,
        "s3Key": key,
        "size": int(obj.get("Size") or 0),
        "updatedAt": updated_at,
    }


def _is_audio_filename(name: str) -> bool:
    return Path(name or "").suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS


def _infer_s3_audio_uri(records: list[dict[str, Any]]) -> str:
    for record in records:
        name = str(record.get("name") or "")
        if "/" not in name and _is_audio_filename(name) and record.get("s3Key"):
            return f"s3://{S3_BUCKET}/{record['s3Key']}"
    for record in records:
        name = str(record.get("name") or "")
        if _is_audio_filename(name) and record.get("s3Key"):
            return f"s3://{S3_BUCKET}/{record['s3Key']}"
    return ""


def _read_text_either(folder: Path, filename: str) -> str:
    # Try local file first
    local = folder / filename
    if local.exists():
        return _read_body_text(local)
    # Then try S3 via metadata mapping
    try:
        metadata = _load_metadata(folder)
        for f in metadata.get("s3Files", []):
            if f.get("name") == filename and f.get("s3Key"):
                txt = get_s3_object_text(S3_BUCKET, f.get("s3Key"), region=AWS_REGION)
                return _strip_saved_text(txt or "")
    except Exception:
        pass
    if S3_BUCKET and filename:
        try:
            key = f"{_storage_prefix_for_folder(folder)}/{filename}".lstrip("/")
            txt = get_s3_object_text(S3_BUCKET, key, region=AWS_REGION)
            if txt is not None:
                return _strip_saved_text(txt)
        except Exception:
            pass
    return ""


def _transcript_item(folder: Path, *, include_content: bool = True) -> dict[str, Any]:
    court_slug, date_value, case_folder = _folder_scope(folder)
    transcript_path = _main_transcript_path(folder)
    edited_path = folder / "edited_transcript.txt"
    final_path = _final_transcript_path(folder)
    summary_path = folder / "summary.txt"
    srt_path = folder / "transcript.srt"
    final_srt_path = folder / "final_transcript.srt"
    metadata = _load_metadata(folder)

    if include_content:
        # Prefer S3-backed content when metadata provides it, otherwise fall back to local files.
        transcript_text = _read_text_either(folder, transcript_path.name if transcript_path else "transcript.txt")
        edited_text = _read_text_either(folder, edited_path.name)
        final_text = _read_text_either(folder, final_path.name)
    else:
        transcript_text = ""
        edited_text = ""
        final_text = ""

    s3_prefix = f"{_storage_prefix(court_slug, date_value, case_folder)}/"
    s3_records: list[dict[str, Any]] = []
    if S3_BUCKET and (not metadata.get("s3Files") or not metadata.get("s3AudioUri") or not metadata.get("originalFileName")):
        s3_records = [
            _s3_record_from_object(obj, s3_prefix)
            for obj in _list_s3_prefix_objects(s3_prefix)
            if obj.get("Key") and not str(obj.get("Key")).endswith("/")
        ]

    translations = []
    # Try translations from local then S3
    for path in sorted(folder.glob("translation_*.txt"), key=lambda p: p.name.lower()):
        key = path.stem.replace("translation_", "", 1)
        language = key.replace("_", " ").title()
        translations.append({
            "filename": path.name,
            "language": language,
            "content": _read_body_text(path) if include_content else "",
        })
    # If S3 metadata holds translations, include them too
    for f in metadata.get("s3Files", []):
        name = f.get("name", "")
        if name.startswith("translation_") and name.endswith(".txt"):
            lang = name.replace("translation_", "").rsplit(".", 1)[0].replace("_", " ").title()
            if not any(t["filename"] == name for t in translations):
                content = (get_s3_object_text(S3_BUCKET, f.get("s3Key"), region=AWS_REGION) or "") if include_content else ""
                translations.append({"filename": name, "language": lang, "content": content})
    for f in s3_records:
        name = str(f.get("name") or "")
        if name.startswith("translation_") and name.endswith(".txt") and not any(t["filename"] == name for t in translations):
            lang = name.replace("translation_", "").rsplit(".", 1)[0].replace("_", " ").title()
            content = (get_s3_object_text(S3_BUCKET, str(f.get("s3Key") or ""), region=AWS_REGION) or "") if include_content else ""
            translations.append({"filename": name, "language": lang, "content": _strip_saved_text(content)})

    # Build files list: prefer metadata.s3Files when present
    files: list[dict[str, Any]] = []
    if metadata.get("s3Files"):
        for f in metadata.get("s3Files", []):
            files.append({
                "name": f.get("name", ""),
                "size": f.get("size", 0),
                "updatedAt": f.get("updatedAt", metadata.get("savedAt", "")),
            })
    elif s3_records:
        files = [
            {
                "name": f.get("name", ""),
                "size": f.get("size", 0),
                "updatedAt": f.get("updatedAt", metadata.get("savedAt", "")),
            }
            for f in s3_records
            if f.get("name")
            and Path(str(f.get("name"))).name != "metadata.json"
            and not _is_audio_filename(str(f.get("name")))
        ]
    else:
        txt_files = sorted(folder.glob("*.txt"), key=lambda p: p.name.lower())
        srt_files = sorted(folder.glob("*.srt"), key=lambda p: p.name.lower())
        files = [_file_record(p) for p in [*txt_files, *srt_files]]

    has_srt = any(f.get("name", "").lower().endswith(".srt") for f in files)
    s3_audio = metadata.get("s3AudioUri") or _infer_s3_audio_uri(s3_records)
    local_audio_path = metadata.get("localAudioPath", "")
    audio_available = bool(s3_audio or (local_audio_path and Path(local_audio_path).exists()))
    audio_file_name = metadata.get("originalFileName") or (s3_audio.rsplit("/", 1)[-1] if s3_audio else "")
    source_file = (
        metadata.get("gcsUri")
        or s3_audio
    )
    if include_content and not source_file:
        source_file = _extract_source_file(_read_text(folder / (transcript_path.name if transcript_path else "transcript.txt")))

    latest_mtime = None
    if files:
        latest_mtime = next((f.get("updatedAt") for f in files if f.get("updatedAt")), None)
    if not latest_mtime and s3_records:
        latest_mtime = next((f.get("updatedAt") for f in s3_records if f.get("updatedAt")), None)
    if not latest_mtime:
        try:
            latest_mtime = datetime.fromtimestamp(folder.stat().st_mtime, tz=COLOMBO_TIMEZONE).isoformat(timespec="seconds")
        except Exception:
            latest_mtime = _now_colombo().isoformat(timespec="seconds")

    all_audio_files = [
        {"name": str(obj.get("name") or ""), "s3Key": str(obj.get("s3Key") or "")}
        for obj in s3_records
        if _is_audio_filename(str(obj.get("name") or ""))
]
    # Also check metadata s3Files for audio
    for f in metadata.get("s3Files", []):
        name = str(f.get("name") or "")
        if _is_audio_filename(name) and not any(a["name"] == name for a in all_audio_files):
            all_audio_files.append({"name": name, "s3Key": str(f.get("s3Key") or "")})


    # also check s3AudioUris stored in metadata
    for uri in metadata.get("s3AudioUris", []):
        name = uri.rsplit("/", 1)[-1]
        s3_key = key_from_storage_uri(uri)
        if name and not any(a["name"] == name for a in all_audio_files):
            all_audio_files.append({"name": name, "s3Key": s3_key}) 

    file_names = {str(f.get("name") or "").lower() for f in files}
    srt_file = next((f.get("name") for f in files if f.get("name", "").lower().endswith(".srt")), "")
    summary_text = _read_text_either(folder, summary_path.name) if include_content else ""
    srt_text = _read_text_either(folder, srt_file) if include_content and srt_file else ""
    has_edited = bool(edited_text) or "edited_transcript.txt" in file_names
    has_finalized = bool(final_text) or bool(metadata.get("finalized")) or "final_transcript.txt" in file_names
    has_summary = bool(summary_text) or "summary.txt" in file_names

    return {
        "id": _encode_id(date_value, folder.name),
        "date": date_value,
        "folderName": case_folder,
        "title": metadata.get("caseNumber") or folder.name,
        "caseNumber": metadata.get("caseNumber", ""),
        "court": metadata.get("court", ""),
        "courtSlug": metadata.get("courtSlug", court_slug),
        "uploadedBy": metadata.get("uploadedBy", ""),
        "sourceFile": source_file,
        "audioFileName": audio_file_name,
        "model": metadata.get("model", _selected_transcription_model()),
        "quality": metadata.get("quality", _selected_transcription_quality()),
        "coverageAudit": bool(metadata.get("coverageAudit")),
        "coverageCheckpoints": metadata.get("coverageCheckpoints", []),
        "updatedAt": latest_mtime,
        "contentLoaded": include_content,
        "transcriptFile": transcript_path.name if transcript_path else "",
        "transcriptText": transcript_text,
        "editedTranscriptText": edited_text,
        "finalizedTranscriptText": final_text,
        "summaryText": summary_text,
        "translations": translations,
        "files": files,
        "hasEditedTranscript": has_edited,
        "hasFinalizedTranscript": has_finalized,
        "finalized": bool(metadata.get("finalized")),
        "finalizedAt": metadata.get("finalizedAt", ""),
        "hasSummary": has_summary,
        "proceedingAvailable": metadata["proceedingAvailable"] if "proceedingAvailable" in metadata else has_summary,
        "hasSrt": has_srt,
        "srtFile": srt_file,
        "srtText": srt_text,
        "audioAvailable": audio_available,
        "s3AudioUri": s3_audio,
        "s3Files": metadata.get("s3Files", []),
        "audioFiles": all_audio_files, 
        "storagePath": metadata.get("storagePath", f"{_storage_prefix(court_slug, date_value, case_folder)}/"),
    }

def _write_metadata(
    folder_name: str,
    *,
    case_number: str,
    hearing_date: str,
    court: str,
    court_slug: str,
    uploaded_by: str,
    original_file_name: str,
    gcs_uri: str,
    translate_langs: list[str],
    include_summary: bool,
    local_audio_path: str = "",
    s3_files: list[dict] | None = None,
    s3_audio_uri: str = "",
    s3_audio_uris: list[str] | None = None,
    coverage_audited: bool = False,
    audio_duration_seconds: float = 0.0,
    token_usage: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not folder_name:
        return None

    folder = TRANSCRIPTS_DIR / court_slug / str(hearing_date) / folder_name
    metadata = {
        "caseNumber": case_number,
        "hearingDate": hearing_date,
        "court": court,
        "courtSlug": court_slug,
        "uploadedBy": uploaded_by,
        "originalFileName": original_file_name,
        "gcsUri": gcs_uri,
        "translationLanguages": translate_langs,
        "includeSummary": include_summary,
        "localAudioPath": local_audio_path,
        "finalized": False,
        "finalizedAt": "",
        "quality": _selected_transcription_quality(),
        "model": _selected_transcription_model(),
        "coverageAudit": coverage_audited,
        "coverageCheckpoints": ["start", "25%", "50%", "75%", "end"] if coverage_audited else [],
        "savedAt": _now_colombo().isoformat(timespec="seconds"),
        "storagePath": f"{_storage_prefix(court_slug, hearing_date, folder_name)}/",
        "audioDurationSeconds": round(audio_duration_seconds, 1),
        "tokenUsage": token_usage or _empty_token_usage(),
    }
    if s3_files:
        metadata["s3Files"] = s3_files
    if s3_audio_uri:
        metadata["s3AudioUri"] = s3_audio_uri
    if s3_audio_uris:                            
        metadata["s3AudioUris"] = s3_audio_uris
    _save_metadata_file(folder, metadata)
    return _transcript_item(folder)


def _clean_docx_text(text: str) -> str:
    cleaned = CONTROL_CHARS.sub("", text or "")
    return cleaned.replace("\r\n", "\n").replace("\r", "\n")


def _parse_transcript_docx_line(raw_line: str) -> tuple[str, str, str]:
    line = raw_line.strip()
    time_label = ""
    speaker = ""
    text = line

    time_match = re.match(
        r"^\[?\s*(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:[\.,]\d{1,3})?\s*(?:-->|-|\u2013|\u2014|to)\s*"
        r"\d{1,2}:\d{2}(?::\d{2})?(?:[\.,]\d{1,3})?)\s*\]?\s*(?P<rest>.*)$",
        line,
        re.IGNORECASE,
    )
    if time_match:
        time_label = re.sub(r"\s+", " ", time_match.group("time").strip())
        text = time_match.group("rest").strip()

    speaker_match = re.match(r"^\[(?P<speaker>[^\]]{1,80})\]\s*:?\s*(?P<text>.*)$", text)
    if speaker_match:
        speaker = speaker_match.group("speaker").strip()
        text = speaker_match.group("text").strip()

    return time_label, speaker, text


def _build_docx(title: str, item: dict[str, Any], version_label: str, content: str) -> BytesIO:
    try:
        from docx import Document
        from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Inches, Pt, RGBColor
    except Exception as exc:
        raise HTTPException(status_code=500, detail="python-docx is not installed.") from exc

    def set_rfonts(element: Any, font_name: str) -> None:
        rpr = element.get_or_add_rPr()
        rfonts = rpr.rFonts
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        for key in ("ascii", "hAnsi", "eastAsia", "cs"):
            rfonts.set(qn(f"w:{key}"), font_name)

    def add_run(
        paragraph: Any,
        text_value: str,
        *,
        font_name: str = "Iskoola Pota",
        size: float = 10.5,
        bold: bool = False,
        color: tuple[int, int, int] | None = None,
    ) -> Any:
        run = paragraph.add_run(text_value)
        run.font.name = font_name
        run.font.size = Pt(size)
        run.bold = bold
        if color:
            run.font.color.rgb = RGBColor(*color)
        set_rfonts(run._element, font_name)
        return run

    def shade_cell(cell: Any, fill: str) -> None:
        tc_pr = cell._tc.get_or_add_tcPr()
        shade = OxmlElement("w:shd")
        shade.set(qn("w:fill"), fill)
        tc_pr.append(shade)

    def set_cell_text(
        cell: Any,
        text_value: str,
        *,
        bold: bool = False,
        size: float = 9.5,
        font_name: str = "Iskoola Pota",
        color: tuple[int, int, int] | None = None,
        align: Any | None = None,
    ) -> None:
        cell.text = ""
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
        paragraph = cell.paragraphs[0]
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.line_spacing = 1.12
        if align is not None:
            paragraph.alignment = align
        add_run(paragraph, text_value, font_name=font_name, size=size, bold=bold, color=color)

    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.72)
    section.bottom_margin = Inches(0.72)
    section.left_margin = Inches(0.7)
    section.right_margin = Inches(0.7)

    normal = document.styles["Normal"]
    normal.font.name = "Iskoola Pota"
    normal.font.size = Pt(10.5)
    set_rfonts(normal._element, "Iskoola Pota")

    title_text = (title or version_label or "Court Transcript").upper()
    source = item.get("sourceFile", "")
    meta_rows = [
        ("Document", version_label),
        ("Case Number", item.get("caseNumber") or item.get("title") or item.get("folderName", "")),
        ("Court", item.get("court", "")),
        ("Hearing Date", item.get("date", "")),
        ("Uploaded By", item.get("uploadedBy", "")),
        ("Generated", _now_colombo().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    
    kicker = document.add_paragraph()
    kicker.alignment = WD_ALIGN_PARAGRAPH.CENTER
    kicker.paragraph_format.space_after = Pt(2)
    add_run(kicker, "COURT AUDIO TRANSCRIPTION SYSTEM", font_name="Times New Roman", size=10, bold=True, color=(94, 104, 116))

    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    heading.paragraph_format.space_after = Pt(8)
    add_run(heading, title_text, font_name="Times New Roman", size=16, bold=True, color=(0, 31, 63))

    meta_table = document.add_table(rows=0, cols=2)
    meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    meta_table.style = "Table Grid"
    for label, value in meta_rows:
        if value is None:
            value = ""
        row = meta_table.add_row()
        row.cells[0].width = Inches(1.55)
        row.cells[1].width = Inches(5.55)
        shade_cell(row.cells[0], "E8EEF4")
        set_cell_text(row.cells[0], label.upper(), bold=True, size=8.8, font_name="Times New Roman", color=(0, 43, 82))
        set_cell_text(row.cells[1], str(value), size=9.2)

    document.add_paragraph()

    transcript_heading = document.add_paragraph()
    transcript_heading.alignment = WD_ALIGN_PARAGRAPH.LEFT
    transcript_heading.paragraph_format.space_before = Pt(4)
    transcript_heading.paragraph_format.space_after = Pt(6)
    add_run(transcript_heading, "TRANSCRIPT", font_name="Times New Roman", size=11, bold=True, color=(0, 43, 82))

    transcript_lines = [line for line in _clean_docx_text(content).splitlines() if line.strip()]
    if not transcript_lines:
        empty = document.add_paragraph()
        add_run(empty, "No transcript content available.", size=10)
    else:
        for raw_line in transcript_lines:
            para = document.add_paragraph()
            para.paragraph_format.space_after = Pt(4)
            para.paragraph_format.space_before = Pt(0)
            para.paragraph_format.line_spacing = 1.15

            # Check if line has a speaker label like [Speaker 1]: text
            speaker_match = re.match(r'^\[(?P<speaker>[^\]]+)\]:\s*(?P<text>.*)$', raw_line.strip())
            if speaker_match:
                speaker = speaker_match.group('speaker').strip()
                text = speaker_match.group('text').strip()
                add_run(para, f'[{speaker}]: ', font_name='Iskoola Pota', size=10, bold=True, color=(0, 43, 82))
                add_run(para, text, font_name='Iskoola Pota', size=10)
            else:
                add_run(para, raw_line.strip(), font_name='Iskoola Pota', size=10)

    out = BytesIO()
    document.save(out)
    out.seek(0)
    return out

def _process_upload_job(
    job_id: str,
    local_paths: list[str],
    workspace: str,
    *,
    case_number: str,
    case_folder: str,
    hearing_date: str,
    court: str,
    court_slug: str,
    uploaded_by: str,
    uploader_uid: str,
    stored_audio_names: list[str],
    translate_langs: list[str],
    include_summary: bool,
) -> None:
    """
    Phase 1 only: save original audio to S3, prepare chunks in GCS,
    submit the Vertex AI batch job, save the job reference, EXIT.
    A background poller (_poll_vertex_jobs) handles Phase 2 (collect + save).
    """
    court_sem = _get_court_semaphore(court_slug)
    _set_job(job_id, status="queued",
             message="Upload receiving - waiting for processing slot")
    counts = _job_counts()
    _log_event(
        "Info", "Queue",
        "Job queued for processing",
        f"queued={counts['queued']} · active={counts['active']} · "
        f"vertex={counts['vertex']} · court={court_slug}",
        job_id=job_id,
        case_number=case_number,
        court=court,
        uploaded_by=uploaded_by,
    )

    queue_start = time.time()
    audio_processing_slots.acquire(blocking=True)
    court_sem.acquire(blocking=True)

    wait_seconds = round(time.time() - queue_start, 1)
    counts = _job_counts()
    _log_event(
        "Info", "Queue",
        "Processing slot acquired - starting Phase 1",
        f"waited={wait_seconds}s · queued={counts['queued']} · "
        f"active={counts['active']} · court={court_slug}",
        job_id=job_id,
        case_number=case_number,
        court=court,
        uploaded_by=uploaded_by,
    )
    _set_job(job_id, status="saving_to_s3",
             message="Processing started")
    try:
        if not INPUT_BUCKET or not OUTPUT_BUCKET:
            raise RuntimeError("GCS_INPUT_BUCKET and GCS_OUTPUT_BUCKET must be configured.")

        audio_uris = []
        s3_audio_uris = []
        total_files = len(local_paths)
        total_audio_duration_seconds = 0.0

        for file_index, (local_path, stored_audio_name) in enumerate(zip(local_paths, stored_audio_names), start=1):
            suffix = f" ({file_index}/{total_files})" if total_files > 1 else ""
            try:
                audio_key = f"{_storage_prefix(court_slug, hearing_date, case_folder)}/{stored_audio_name}"
                _set_job(
                    job_id,
                    status="saving_to_s3",
                    message=f"Saving original audio to S3{suffix}",
                    activeFile=stored_audio_name,
                )
                s3_start = time.time()
                s3_audio_uris.append(upload_file_to_s3(S3_BUCKET, audio_key, local_path, region=AWS_REGION))
                _set_job(
                    job_id,
                    status="uploaded_to_s3",
                    message=f"Original audio saved to S3{suffix}",
                    s3AudioUri=s3_audio_uris[0] if s3_audio_uris else "",
                    s3AudioUris=s3_audio_uris,
                    activeFile=stored_audio_name,
                )
                _log_event(
                    "Info", "S3",
                    f"Audio saved to S3{suffix}",
                    stored_audio_name,
                    job_id=job_id, case_number=case_number,
                    court=court, uploaded_by=uploaded_by,
                    duration_seconds=round(time.time() - s3_start, 1),
                )

                _step_times: dict[str, float] = {}
                _prev_status: list[str | None] = [None]
                _step_event_names = {
                    "enhancing_audio":         ("Audio Processing", "Audio enhanced for speech clarity"),
                    "preparing_audio":         ("Audio Processing", "Audio converted to FLAC"),
                    "chunking_audio":          ("Audio Processing", "Audio split into transcript chunks"),
                    "uploading_chunks_to_gcs": ("GCS",             "Audio chunks uploaded to Google Cloud"),
                }

                def _audio_progress(status: str, message: str) -> None:
                    now = time.time()
                    prev = _prev_status[0]
                    # When a new step begins, log completion of the previous one with its duration
                    if prev and prev != status and prev in _step_times and prev in _step_event_names:
                        svc, evt = _step_event_names[prev]
                        _log_event(
                            "Info", svc, evt, stored_audio_name,
                            job_id=job_id, case_number=case_number,
                            court=court, uploaded_by=uploaded_by,
                            duration_seconds=round(now - _step_times[prev], 1),
                        )
                    # Record start time only on first occurrence of each step
                    if status not in _step_times:
                        _step_times[status] = now
                    _prev_status[0] = status
                    _set_job(
                        job_id,
                        status=status,
                        message=f"{message}{suffix}",
                        activeFile=stored_audio_name,
                    )

                uploaded_uris, file_duration_seconds = upload_audio_file(
                    local_path,
                    INPUT_BUCKET,
                    prefix="uploads/manual",
                    convert_to_flac=True,
                    enhance_audio=ENHANCE_AUDIO_ON_UPLOAD,
                    chunk_audio=CHUNK_AUDIO_ON_UPLOAD,
                    progress_callback=_audio_progress,
                    work_dir=workspace,
                )
                # Log the final step's completion after upload_audio_file returns
                _final = _prev_status[0]
                if _final and _final in _step_times and _final in _step_event_names:
                    svc, evt = _step_event_names[_final]
                    _log_event(
                        "Info", svc, evt, stored_audio_name,
                        job_id=job_id, case_number=case_number,
                        court=court, uploaded_by=uploaded_by,
                        duration_seconds=round(time.time() - _step_times[_final], 1),
                    )

                if uploaded_uris:
                    audio_uris.extend(uploaded_uris)
                total_audio_duration_seconds += file_duration_seconds
            finally:
                try:
                    Path(local_path).unlink()
                except Exception:
                    pass

        if not audio_uris:
            raise RuntimeError("No GCS URI returned after upload.")
        gcs_uri = audio_uris[0]
        chunk_group_count, chunk_count = count_chunk_groups(audio_uris)
        if chunk_count:
            print(f"[upload] Prepared {chunk_count} chunk(s) from {chunk_group_count} source file(s)")

        # 3. Build + submit Vertex AI batch job
        _set_job(job_id, status="submitted_to_vertex", message="Submitting transcription request to Vertex AI")
        timestamp = _now_colombo().strftime("%Y%m%d_%H%M%S")
        jsonl_name = f"api_transcription_{timestamp}_{job_id}.jsonl"
        jsonl_local = str(Path(workspace) / "batch" / jsonl_name)
        jsonl_gcs_blob = f"batch_inputs/{jsonl_name}"
        transcription_quality = _selected_transcription_quality()
        transcription_model = _selected_transcription_model()

        transcription_entries = build_transcription_jsonl(audio_uris, jsonl_local, quality=transcription_quality)
        if not transcription_entries:
            _set_job(job_id, status="merging_transcript", message="Saving hearing issue markers")
            skipped_transcripts = _add_skipped_chunk_markers(audio_uris, {})
            parent_audio_uris, skipped_transcripts = merge_chunk_transcripts(audio_uris, skipped_transcripts)
            if parent_audio_uris:
                audio_uris = parent_audio_uris
                gcs_uri = audio_uris[0]
            folder_name, s3_files = save_results_locally(
                audio_uris, skipped_transcripts, [], False,
                output_dir=str(TRANSCRIPTS_DIR),
                result_date=hearing_date,
                court=court_slug,
                original_filename=stored_audio_names[0],
                case_folder=case_folder,
            )
            item = _write_metadata(
                folder_name,
                case_number=case_number,
                hearing_date=hearing_date,
                court=court,
                court_slug=court_slug,
                uploaded_by=uploaded_by,
                original_file_name=stored_audio_names[0],
                gcs_uri=gcs_uri,
                translate_langs=[],
                include_summary=False,
                local_audio_path="",
                s3_files=s3_files,
                s3_audio_uri=s3_audio_uris[0] if s3_audio_uris else "",
                s3_audio_uris=s3_audio_uris,
                coverage_audited=False,
                audio_duration_seconds=total_audio_duration_seconds,
            )
            _set_job(
                job_id,
                status="completed",
                message="Transcript is ready",
                transcript=item,
                savedFiles=s3_files,
                court=court,
                courtSlug=court_slug,
                uploaderUid=uploader_uid,
            )
            return
        try:
            jsonl_gcs_uri = upload_to_gcs(jsonl_local, INPUT_BUCKET, jsonl_gcs_blob)
        finally:
            safe_unlink(jsonl_local)
        output_gcs_prefix = f"gs://{OUTPUT_BUCKET}/batch_outputs/{timestamp}_{job_id}_transcription/"

        vertex_job, _ = submit_batch_job(
            jsonl_gcs_uri, output_gcs_prefix, model_override=transcription_model
        )
        _set_job(job_id, status="submitted", message="Audio submitted to Vertex AI")
        _log_event(
            "Info", "Vertex AI",
            f"Batch transcription job submitted - {chunk_count} chunk(s)" if chunk_count else "Batch transcription job submitted",
            f"model: {transcription_model} · quality: {transcription_quality}",
            job_id=job_id, case_number=case_number,
            court=court, uploaded_by=uploaded_by,
        )

        # 4. Save everything needed for the poller to finish the job later
        #    — DO NOT WAIT. Thread exits here.
        _set_job(
            job_id,
            status="processing",               # poller watches for this status
            message="Vertex AI is transcribing in the background",
            gcsUri=gcs_uri,
            audioUris=audio_uris,
            chunkGroupCount=chunk_group_count,
            audioChunkCount=chunk_count,
            audioDurationSeconds=total_audio_duration_seconds,
            s3AudioUri=s3_audio_uris[0] if s3_audio_uris else "",
            s3AudioUris=s3_audio_uris,
            vertexJobName=vertex_job.name,     # durable Vertex AI job identifier
            outputGcsPrefix=output_gcs_prefix,
            caseNumber=case_number,
            caseFolder=case_folder,
            hearingDate=hearing_date,
            court=court,
            courtSlug=court_slug,
            uploadedBy=uploaded_by,
            uploaderUid=uploader_uid,
            storedAudioName=stored_audio_names[0],
            storedAudioNames=stored_audio_names,
            transcriptionQuality=transcription_quality,
            transcriptionModel=transcription_model,
            timestamp=timestamp,
        )

    except Exception as exc:
        public_error = classify_exception(exc)
        _set_job(job_id, status="failed", message=public_error.message, errorCode=public_error.code, error={
            "code": public_error.code, "message": public_error.message,
            "category": public_error.category, "retryable": public_error.retryable,
        })
        _log_event(
            "Error", "API Server",
            "Upload job failed",
            str(exc)[:200],
            job_id=job_id, case_number=case_number,
            court=court, uploaded_by=uploaded_by,
        )
    finally:
        try:
            court_sem.release()
        except Exception:
            pass
        try:
            audio_processing_slots.release()
        except Exception:
            pass

        try:
            counts = _job_counts()
            _log_event(
                "Info", "Queue",
                "Processing slot released",
                f"queued={counts['queued']} · active={counts['active']} · "
                f"vertex={counts['vertex']} · court={court_slug}",
                job_id=job_id,
                case_number=case_number,
                court=court,
                uploaded_by=uploaded_by,
            )
        except Exception:
            pass
        
        try:
            reclaimed = cleanup_job_workspace(workspace)
            _log_event(
                "Info", "Temporary Storage", "Upload workspace cleaned",
                f"reclaimedBytes={reclaimed}", job_id=job_id,
                case_number=case_number, court=court, uploaded_by=uploaded_by,
            )
        except Exception as cleanup_exc:
            _log_event("Warning", "Temporary Storage", "Upload workspace cleanup failed", str(cleanup_exc)[:160], job_id=job_id)
        
def _write_finalized_transcript(folder: Path, content: str) -> dict[str, Any]:
    content = (content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Final transcript cannot be empty.")

    metadata = _load_metadata(folder)
    source = metadata.get("gcsUri") or _extract_source_file(_read_text(_main_transcript_path(folder)))

    # Upload final transcript and SRT to S3.
    s3_entries = []
    try:
        prefix = _storage_prefix_for_folder(folder)
        final_key = f"{prefix}/final_transcript.txt"
        upload_text(S3_BUCKET, final_key, content, content_type="text/plain; charset=utf-8", region=AWS_REGION)
        s3_entries.append({"name": "final_transcript.txt", "s3Key": final_key, "size": len(content.encode("utf-8")), "updatedAt": _now_colombo().isoformat(timespec="seconds")})
        srt_text = transcript_text_to_srt(content)
        if srt_text:
            srt_key = f"{prefix}/final_transcript.srt"
            upload_text(S3_BUCKET, srt_key, srt_text, content_type="application/x-subrip; charset=utf-8", region=AWS_REGION)
            s3_entries.append({"name": "final_transcript.srt", "s3Key": srt_key, "size": len(srt_text.encode("utf-8")), "updatedAt": _now_colombo().isoformat(timespec="seconds")})
    except Exception as exc:
        raise RuntimeError(f"Could not upload finalized transcript to S3: {exc}") from exc

    # Update metadata
    _update_metadata(
        folder,
        finalized=True,
        finalizedAt=_now_colombo().isoformat(timespec="seconds"),
    )
    # Merge s3 entries into metadata.s3Files
    try:
        meta = _load_metadata(folder)
        files = meta.get("s3Files", [])
        # replace any final entries
        files = [f for f in files if f.get("name") not in ("final_transcript.txt", "final_transcript.srt")]
        files.extend(s3_entries)
        _update_metadata(folder, s3Files=files)
    except Exception:
        pass

    return _transcript_item(folder)


def _write_generated_text_outputs(
    folder: Path,
    source: str,
    results: dict[str, str],
    filename_stem: str,
    translate_langs: list[str],
    include_summary: bool,
) -> bool:
    s3_entries: list[dict[str, Any]] = []
    prefix = _storage_prefix_for_folder(folder)
    for lang in translate_langs:
        key = f"0|{filename_stem}|translate_{lang}"
        text = results.get(key, "").strip()
        if not text:
            continue
        lang_name = LANGUAGE_NAMES[lang]
        name = f"translation_{lang_name.lower()}.txt"
       # header = build_header(source, f"TRANSLATION - {lang_name.upper()}", f"Full translation of the verified transcript into {lang_name}")
       # body = header + "\n" + text
        try:
            s3_key = f"{prefix}/{name}"
            upload_text(S3_BUCKET, s3_key, text, content_type="text/plain; charset=utf-8", region=AWS_REGION)
            s3_entries.append({"name": name, "s3Key": s3_key, "size": len(text.encode("utf-8")), "updatedAt": _now_colombo().isoformat(timespec="seconds")})
        except Exception:
            # S3 failed — skip local write to enforce S3-only storage
            pass

    real_proceeding = False
    if include_summary:
        key = f"0|{filename_stem}|summary"
        text = results.get(key, "").strip()
        if not text:
            text = (
                "No court proceeding format was identified in this transcript.\n\n"
                "This transcript may be an administrative session, case call, or "
                "short hearing that does not contain examination questions and answers.\n\n"
                "The original transcript is available in the Transcript tab."
            )
        else:
            real_proceeding = True

        if text:
            name = "summary.txt"
           # header = build_header(source, "CASE SUMMARY", "Summary of the verified court transcript")
          #  body = header + "\n" + text
            try:
                s3_key = f"{prefix}/{name}"
                upload_text(S3_BUCKET, s3_key, text, content_type="text/plain; charset=utf-8", region=AWS_REGION)
                s3_entries.append({"name": name, "s3Key": s3_key, "size": len(text.encode("utf-8")), "updatedAt": _now_colombo().isoformat(timespec="seconds")})
            except Exception:
                # S3 failed — skip local write to enforce S3-only storage
                pass

    if s3_entries:
        try:
            meta = _load_metadata(folder)
            files = meta.get("s3Files", [])
            # remove any duplicates
            existing_names = {f.get("name") for f in files}
            for e in s3_entries:
                if e.get("name") not in existing_names:
                    files.append(e)
            _update_metadata(folder, s3Files=files)
        except Exception:
            pass

    return real_proceeding

def _process_post_processing_job(
    job_id: str,
    item_id: str,
    court_slug: str,
    translate_langs: list[str],
    include_summary: bool,
) -> None:
    workspace: Path | None = None
    try:
        workspace = create_job_workspace(f"{job_id}-post")
        if not translate_langs and not include_summary:
            raise RuntimeError("Select at least one translation language or proceeding.")
        if not INPUT_BUCKET or not OUTPUT_BUCKET:
            raise RuntimeError("GCS_INPUT_BUCKET and GCS_OUTPUT_BUCKET must be configured in backend/.env")

        folder = _resolve_item_dir(item_id, court_slug)
        metadata = _load_metadata(folder)
        if not metadata.get("finalized"):
            raise RuntimeError("Verify and finalize the transcript before generating proceeding or translations.")

        final_path = _final_transcript_path(folder)
        transcript_text = _read_text_either(folder, final_path.name)
        if not transcript_text:
            raise RuntimeError("Final transcript is empty.")

        source = metadata.get("gcsUri") or _extract_source_file(_read_text(_main_transcript_path(folder))) or folder.name
        filename_stem = source.split("/")[-1].rsplit(".", 1)[0]
        transcript_key = f"0|{filename_stem}|transcript"
        timestamp = _now_colombo().strftime("%Y%m%d_%H%M%S")

        pp_start = time.time()
        zero_usage = {"audioInput": 0, "textInput": 0, "textOutput": 0, "thinkingTokens": 0, "cachedTokens": 0, "total": 0}
        summary_usage = dict(zero_usage)
        translation_usage_by_lang: dict[str, dict] = {lang: dict(zero_usage) for lang in translate_langs}
        results: dict[str, str] = {}

        # Step 1: Generate proceeding from transcript
        proceeding_text = ""
        if include_summary:
            transcript_chunks = _split_transcript_for_proceeding(transcript_text)
            num_chunks = len(transcript_chunks)
            chunk_label = f" ({num_chunks} parts)" if num_chunks > 1 else ""

            if num_chunks > 1:
                _log_event(
                    "Info", "API Server",
                    f"Transcript split into {num_chunks} chunks for proceeding generation",
                    f"total chars: {len(transcript_text):,} · "
                    f"chunk size: 20,000 chars · "
                    f"case: {metadata.get('caseNumber', '')}",
                    job_id=job_id,
                    case_number=metadata.get("caseNumber", ""),
                    court=metadata.get("court", ""),
                    uploaded_by=metadata.get("uploadedBy", ""),
                )
            
            _set_job(
                job_id,
                status="post_processing",
                message=f"Generating proceeding from verified transcript{chunk_label}",
            )

            jsonl_name     = f"api_summary_{timestamp}_{job_id}.jsonl"
            jsonl_local    = str(workspace / "batch" / jsonl_name)
            jsonl_gcs_blob = f"batch_inputs/{jsonl_name}"

            if num_chunks == 1:
                # short transcript — existing behaviour unchanged
                build_text_jsonl(
                    [source], {transcript_key: transcript_text},
                    [], True, jsonl_local,
                )
            else:
                # long transcript — one entry per chunk, all run in parallel
                build_proceeding_chunks_jsonl(transcript_chunks, jsonl_local)

            try:
                jsonl_gcs_uri = upload_to_gcs(jsonl_local, INPUT_BUCKET, jsonl_gcs_blob)
            finally:
                safe_unlink(jsonl_local)

            summary_prefix = f"gs://{OUTPUT_BUCKET}/batch_outputs/{timestamp}_{job_id}_summary/"
            job, client = submit_batch_job(jsonl_gcs_uri, summary_prefix, model_override=FAST_MODEL)
            success, job = wait_for_batch_job(job, client)
            if not success:
                raise RuntimeError("Proceeding generation failed.")

            summary_raw           = download_and_parse_results(summary_prefix)
            summary_usage_entries = {k: v for k, v in summary_raw.items() if k.endswith("__usage")}
            summary_usage         = _sum_usage_entries(summary_usage_entries) if summary_usage_entries else zero_usage
            summary_results       = {k: v for k, v in summary_raw.items() if not k.endswith("__usage")}

            if num_chunks == 1:
                # short transcript — original key lookup
                summary_key     = f"0|{filename_stem}|summary"
                proceeding_text = (summary_results.get(summary_key, "") or "").strip()
                results.update(summary_results)
            else:
                # long transcript — collect parts in order, skip empty chunks
                NO_QA_MARKER = "No court proceeding format"
                parts: list[str] = []
                for idx in range(num_chunks):
                    part = (summary_results.get(f"proceeding_chunk_{idx}", "") or "").strip()
                    if part and not part.startswith(NO_QA_MARKER):
                        parts.append(part)
                proceeding_text = "\n\n".join(parts)
                # store under canonical key so Step 2 (translations) works unchanged
                results[f"0|{filename_stem}|summary"] = proceeding_text.strip()

                _log_event(
                    "Info", "API Server",
                    f"Proceeding chunks merged - {len(parts)}/{num_chunks} chunks had Q&A content",
                    f"total proceeding chars: {len(proceeding_text):,} · "
                    f"case: {metadata.get('caseNumber', '')}",
                    job_id=job_id,
                    case_number=metadata.get("caseNumber", ""),
                    court=metadata.get("court", ""),
                    uploaded_by=metadata.get("uploadedBy", ""),
                )

        # Step 2: Generate translations from proceeding
        if translate_langs:
            if not proceeding_text:
                proceeding_text = _read_text_either(folder, "summary.txt")
            if not proceeding_text or proceeding_text.startswith(
                "No court proceeding format was identified"
            ):
                _log_event(
                    "Info", "API Server",
                    "Translations skipped - no proceeding content available",
                    f"case: {metadata.get('caseNumber', '')}",
                    job_id=job_id,
                    case_number=metadata.get("caseNumber", ""),
                    court=metadata.get("court", ""),
                    uploaded_by=metadata.get("uploadedBy", ""),
                )
                translate_langs = []

            else:
                # ── Build per-language chunks using per-language sizes ──
                lang_chunks: dict[str, list[str]] = {}
                lang_chunk_counts: dict[str, int] = {}
                for lang in translate_langs:
                    max_chars = TRANSLATION_CHUNK_SIZES.get(lang, 20_000)
                    chunks = _split_transcript_for_proceeding(
                        proceeding_text, max_chars=max_chars,
                    )
                    lang_chunks[lang] = chunks
                    lang_chunk_counts[lang] = len(chunks)

                max_chunks = max(lang_chunk_counts.values()) if lang_chunk_counts else 1
                any_chunked = any(n > 1 for n in lang_chunk_counts.values())
                chunk_label = f" ({max_chunks} parts)" if any_chunked else ""

                if any_chunked:
                    _log_event(
                        "Info", "API Server",
                        "Proceeding split into per-language chunks for translation",
                        " · ".join(
                            f"{lang}={lang_chunk_counts[lang]}"
                            for lang in translate_langs
                        ) + f" · case: {metadata.get('caseNumber', '')}",
                        job_id=job_id,
                        case_number=metadata.get("caseNumber", ""),
                        court=metadata.get("court", ""),
                        uploaded_by=metadata.get("uploadedBy", ""),
                    )

                _set_job(
                    job_id,
                    status="post_processing",
                    message=f"Generating translations from proceeding{chunk_label}",
                )

                jsonl_name     = f"api_translations_{timestamp}_{job_id}.jsonl"
                jsonl_local    = str(workspace / "batch" / jsonl_name)
                jsonl_gcs_blob = f"batch_inputs/{jsonl_name}"

                if not any_chunked:
                    # all languages fit in one chunk — existing behaviour unchanged
                    translation_source_key = f"0|{filename_stem}|transcript"
                    build_text_jsonl(
                        [source],
                        {translation_source_key: proceeding_text},
                        translate_langs, False, jsonl_local,
                    )
                else:
                    # per-language chunks — one entry per chunk per language
                    build_translation_chunks_jsonl(lang_chunks, jsonl_local)

                try:
                    jsonl_gcs_uri = upload_to_gcs(jsonl_local, INPUT_BUCKET, jsonl_gcs_blob)
                finally:
                    safe_unlink(jsonl_local)

                translation_prefix = (
                    f"gs://{OUTPUT_BUCKET}/batch_outputs/"
                    f"{timestamp}_{job_id}_translations/"
                )
                job, client = submit_batch_job(
                    jsonl_gcs_uri, translation_prefix, model_override=FAST_MODEL,
                )
                success, job = wait_for_batch_job(job, client)
                if not success:
                    raise RuntimeError("Translation job failed.")

                translation_raw           = download_and_parse_results(translation_prefix)
                translation_usage_entries = {k: v for k, v in translation_raw.items() if k.endswith("__usage")}
                translation_results       = {k: v for k, v in translation_raw.items() if not k.endswith("__usage")}

                if not any_chunked:
                    # short — original key lookup, unchanged
                    results.update(translation_results)
                    for lang in translate_langs:
                        lang_entries = {
                            k: v for k, v in translation_usage_entries.items()
                            if f"|translate_{lang}" in k
                        }
                        translation_usage_by_lang[lang] = (
                            _sum_usage_entries(lang_entries) if lang_entries else zero_usage
                        )

                else:
                    # per-language chunks — collect each language independently
                    for lang in translate_langs:
                        n = lang_chunk_counts[lang]
                        parts: list[str] = []
                        for idx in range(n):
                            part = (
                                translation_results.get(
                                    f"translation_chunk_{idx}_{lang}", ""
                                ) or ""
                            ).strip()
                            if part:
                                parts.append(part)
                        results[f"0|{filename_stem}|translate_{lang}"] = "\n\n".join(parts)

                        lang_entries = {
                            k: v for k, v in translation_usage_entries.items()
                            if k.endswith(f"_{lang}__usage")
                        }
                        translation_usage_by_lang[lang] = (
                            _sum_usage_entries(lang_entries) if lang_entries else zero_usage
                        )

                    _log_event(
                        "Info", "API Server",
                        "Translation chunks merged per language",
                        " · ".join(
                            f"{lang}={lang_chunk_counts[lang]}"
                            for lang in translate_langs
                        ) + f" · case: {metadata.get('caseNumber', '')}",
                        job_id=job_id,
                        case_number=metadata.get("caseNumber", ""),
                        court=metadata.get("court", ""),
                        uploaded_by=metadata.get("uploadedBy", ""),
                    )

        _set_job(job_id, status="saving", message="Saving proceeding and translations to S3")
        real_proceeding =_write_generated_text_outputs(folder, source, results, filename_stem, translate_langs, include_summary)

        meta_now = _load_metadata(folder)
        existing_usage = meta_now.get("tokenUsage") or _empty_token_usage()
        if include_summary:
            existing_usage["summary"] = _add_usage(existing_usage.get("summary", zero_usage), summary_usage)
        existing_translations = existing_usage.get("translations", {}) or {}
        for lang, usage in translation_usage_by_lang.items():
            prev = existing_translations.get(lang, zero_usage)
            existing_translations[lang] = _add_usage(prev, usage)
        existing_usage["translations"] = existing_translations

        _update_metadata(
            folder,
            translationLanguages=translate_langs,
            includeSummary=include_summary,
            proceedingAvailable=real_proceeding,
            postProcessedAt=_now_colombo().isoformat(timespec="seconds"),
            tokenUsage=existing_usage,
        )
        outputs = []
        if include_summary:
            if real_proceeding:
                outputs.append("proceeding")
            else:
                outputs.append("proceeding (no content found)")
        if translate_langs:
            outputs.extend(translate_langs)
        elif include_summary and not real_proceeding:
            outputs.append("translations skipped")
        
        _log_event(
            "Success" if real_proceeding or translate_langs else "Info", 
            "S3",
            f"Post-processing completed - {', '.join(outputs)}",
            f"saved to S3",
            job_id=job_id,
            case_number=metadata.get("caseNumber", ""),
            court=metadata.get("court", ""),
            uploaded_by=metadata.get("uploadedBy", ""),
            duration_seconds=round(time.time() - pp_start, 1),
        )
        _set_job(job_id, status="completed", message="Proceeding and translations are ready", transcript=_transcript_item(folder), courtSlug=court_slug)
    except Exception as exc:
        public_error = classify_exception(exc)
        user_message = str(exc) if isinstance(exc, RuntimeError) else public_error.message
        _set_job(job_id, status="failed", message=user_message, errorCode=public_error.code, error={
            "code": public_error.code, "message": user_message,
            "category": public_error.category, "retryable": public_error.retryable,
        })
        _log_event(
            "Error", "API Server",
            "Post-processing failed",
            str(exc)[:200],
            job_id=job_id,
            case_number=metadata.get("caseNumber", "") if 'metadata' in dir() else "",
            court=metadata.get("court", "") if 'metadata' in dir() else "",
            uploaded_by=metadata.get("uploadedBy", "") if 'metadata' in dir() else "",
        )
    finally:
        if workspace is not None:
            try:
                cleanup_job_workspace(workspace)
            except Exception as cleanup_exc:
                _log_event("Warning", "Temporary Storage", "Post-processing workspace cleanup failed", str(cleanup_exc)[:160], job_id=job_id)

def _resolve_audio_path(item_id: str, court_slug: str) -> Path:
    folder = _resolve_item_dir(item_id, court_slug)
    metadata = _load_metadata(folder)
    raw_path = metadata.get("localAudioPath")
    if not raw_path:
        raise HTTPException(status_code=404, detail="Audio preview is not available for this transcript.")
    audio_path = Path(raw_path).resolve()
    upload_base = UPLOADS_DIR.resolve()
    try:
        audio_path.relative_to(upload_base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid audio path.") from exc
    if not audio_path.exists() or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file was not found on this machine.")
    return audio_path


def _storage_key_for_named_file(folder: Path, filename: str) -> str:
    safe_name = _safe_filename(filename, "file")
    metadata = _load_metadata(folder)

    for record in metadata.get("s3Files", []):
        if record.get("name") == safe_name:
            key = str(record.get("s3Key") or "")
            if key:
                return key_from_storage_uri(key)

    for uri in metadata.get("s3AudioUris", []):
        if uri.rsplit("/", 1)[-1] == safe_name:
            return key_from_storage_uri(uri)

    return f"{_storage_prefix_for_folder(folder)}/{safe_name}"

@dataclass(frozen=True)
class AuthenticatedAdmin:
    uid: str
    project_id: str
    display_name: str
    token: str


def _require_admin_user(authorization: str | None = Header(default=None)) -> AuthenticatedAdmin:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication is required.")

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authentication is required.")

    unverified = _decode_token_payload(token)
    token_project_id = str(unverified.get("aud") or "").strip()
    expected_project_id = FIREBASE_PROJECT_ID or token_project_id

    try:
        claims = google_id_token.verify_firebase_token(
            token, google_requests.Request(), audience=expected_project_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Authentication token could not be verified.") from exc

    uid = str(claims.get("uid") or claims.get("user_id") or claims.get("sub") or "").strip()
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication token is missing the user id.")

    profile = _fetch_firestore_user(expected_project_id, uid, token)
    role = str(profile.get("role") or "").strip()
    if role != "System Administrator":
        raise HTTPException(status_code=403, detail="Administrator access required.")

    return AuthenticatedAdmin(
        uid=uid,
        project_id=expected_project_id,
        display_name=str(profile.get("displayName") or "").strip(),
        token=token,
    )


def _format_duration(seconds: Any) -> str:
    try:
        total = int(float(seconds or 0))
    except Exception:
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _list_court_slugs() -> list[str]:
    entries = _list_s3_directory_entries(f"{S3_NAMESPACE}/")
    slugs = []
    for entry in entries:
        if str(entry.get("Type") or "").lower() != "directory":
            continue
        slug = str(entry.get("Key") or "").strip("/").rsplit("/", 1)[-1]
        if slug:
            slugs.append(slug)
    return slugs


def _activity_row_from_folder(folder: Path) -> dict[str, Any]:
    metadata = _load_metadata(folder)
    token_usage = metadata.get("tokenUsage") or _empty_token_usage()
    translations_usage = token_usage.get("translations", {}) or {}

    def _total(cat: dict) -> int:
        return int((cat or {}).get("total", 0))

    def _sum_cats(*cats):
        keys = ["audioInput", "textInput", "textOutput", "thinkingTokens", "cachedTokens", "total"]
        out = {k: 0 for k in keys}
        for c in cats:
            c = c or {}
            for k in keys:
                out[k] += c.get(k, 0)
        return out

    def _cat_entry(label: str, cat: dict) -> dict[str, Any]:
        cat = cat or {}
        return {
            "label":         label,
            "audioInput":    cat.get("audioInput", 0),
            "textInput":     cat.get("textInput", 0),
            "textOutput":    cat.get("textOutput", 0),
            "thinkingTokens": cat.get("thinkingTokens", 0),
            "cachedTokens":  cat.get("cachedTokens", 0),
            "total":         cat.get("total", 0),
        }

    original_usage = _sum_cats(
        token_usage.get("transcription"),
        token_usage.get("repair"),
        token_usage.get("audit"),
    )

    token_breakdown: list[dict[str, Any]] = []
    if original_usage.get("total", 0) > 0:
        token_breakdown.append(_cat_entry("Transcript", original_usage))
    if _total(token_usage.get("summary")) > 0:
        token_breakdown.append(_cat_entry("Proceeding", token_usage.get("summary")))
    for lang_code, lang_label in LANGUAGE_NAMES.items():
        lang_usage = translations_usage.get(lang_code)
        if lang_usage and lang_usage.get("total", 0) > 0:
            token_breakdown.append(_cat_entry(lang_label, lang_usage))

    total_tokens = (
        _total(token_usage.get("transcription"))
        + _total(token_usage.get("repair"))
        + _total(token_usage.get("audit"))
        + _total(token_usage.get("summary"))
        + sum(_total(u) for u in translations_usage.values())
    )
    audio_in = (
        token_usage.get("transcription", {}).get("audioInput", 0)
        + token_usage.get("repair", {}).get("audioInput", 0)
        + token_usage.get("audit", {}).get("audioInput", 0)
    )
    text_in = (
        token_usage.get("transcription", {}).get("textInput", 0)
        + token_usage.get("repair", {}).get("textInput", 0)
        + token_usage.get("audit", {}).get("textInput", 0)
        + token_usage.get("summary", {}).get("textInput", 0)
        + sum(u.get("textInput", 0) for u in translations_usage.values())
    )
    text_out = (
        token_usage.get("transcription", {}).get("textOutput", 0)
        + token_usage.get("repair", {}).get("textOutput", 0)
        + token_usage.get("audit", {}).get("textOutput", 0)
        + token_usage.get("summary", {}).get("textOutput", 0)
        + sum(u.get("textOutput", 0) for u in translations_usage.values())
    )
    thinkingTokens = (
        token_usage.get("transcription", {}).get("thinkingTokens", 0)
        + token_usage.get("repair", {}).get("thinkingTokens", 0)
        + token_usage.get("audit", {}).get("thinkingTokens", 0)
        + token_usage.get("summary", {}).get("thinkingTokens", 0)
        + sum(u.get("thinkingTokens", 0) for u in translations_usage.values())
    )
    cachedTokens = (
        token_usage.get("transcription", {}).get("cachedTokens", 0)
        + token_usage.get("repair", {}).get("cachedTokens", 0)
        + token_usage.get("audit", {}).get("cachedTokens", 0)
        + token_usage.get("summary", {}).get("cachedTokens", 0)
        + sum(u.get("cachedTokens", 0) for u in translations_usage.values())
    )

    file_names = {f.get("name") for f in metadata.get("s3Files", [])}

    return {
        "court": metadata.get("court", ""),
        "courtSlug": metadata.get("courtSlug", ""),
        "caseNumber": metadata.get("caseNumber") or folder.name,
        "hearingDate": metadata.get("hearingDate", ""),
        "uploadedBy": metadata.get("uploadedBy", ""),
        "audioDurationSeconds": metadata.get("audioDurationSeconds", 0),
        "audioDurationFormatted": _format_duration(metadata.get("audioDurationSeconds", 0)),
        "totalTokens": total_tokens,
        "audioInputTokens": audio_in,
        "textInputTokens": text_in,
        "textOutputTokens": text_out,
        "thinkingTokens": thinkingTokens,
        "cachedTokens": cachedTokens,
        "tokenBreakdown": token_breakdown,
        "finalized": bool(metadata.get("finalized")),
        "proceedingGenerated": "summary.txt" in file_names,
        "translations": {
            "sinhala": "translation_sinhala.txt" in file_names,
            "tamil": "translation_tamil.txt" in file_names,
            "english": "translation_english.txt" in file_names,
        },
        "savedAt": metadata.get("savedAt", ""),
    }

@app.get("/api/admin/activity-log/dates")
def admin_activity_log_dates(
    admin: AuthenticatedAdmin = Depends(_require_admin_user),
) -> dict[str, Any]:
    if not S3_BUCKET:
        return {"dates": []}

    all_court_slugs = _list_court_slugs()
    dates: set[str] = set()

    for slug in all_court_slugs:
        date_entries = _list_s3_directory_entries(f"{S3_NAMESPACE}/{slug}/")
        for date_entry in date_entries:
            if str(date_entry.get("Type") or "").lower() != "directory":
                continue
            date_value = str(date_entry.get("Key") or "").strip("/").rsplit("/", 1)[-1]
            if not VALID_DATE.match(date_value):
                continue
            case_entries = _list_s3_directory_entries(f"{S3_NAMESPACE}/{slug}/{date_value}/")
            for case_entry in case_entries:
                if str(case_entry.get("Type") or "").lower() != "directory":
                    continue
                case_folder = str(case_entry.get("Key") or "").strip("/").rsplit("/", 1)[-1]
                if not case_folder:
                    continue
                folder = TRANSCRIPTS_DIR / slug / date_value / case_folder
                metadata = _load_metadata(folder)
                saved_at = metadata.get("savedAt", "")
                if saved_at and len(saved_at) >= 10:
                    dates.add(saved_at[:10])

    return {"dates": sorted([{"date": d, "severity": "Info"} for d in dates], key=lambda x: x["date"])}

@app.get("/api/admin/activity-log")
def admin_activity_log(
    court: str | None = None,
    date: str | None = None,
    admin: AuthenticatedAdmin = Depends(_require_admin_user),
) -> dict[str, Any]:
    if not S3_BUCKET:
        return {"rows": [], "courts": []}

    all_court_slugs = _list_court_slugs()
    court_slugs = [court] if court else all_court_slugs
    folders: list[Path] = []

    for slug in court_slugs:
        date_entries = _list_s3_directory_entries(f"{S3_NAMESPACE}/{slug}/")
        for date_entry in date_entries:
            if str(date_entry.get("Type") or "").lower() != "directory":
                continue
            date_value = str(date_entry.get("Key") or "").strip("/").rsplit("/", 1)[-1]
            if not VALID_DATE.match(date_value):
                continue
            case_entries = _list_s3_directory_entries(f"{S3_NAMESPACE}/{slug}/{date_value}/")
            for case_entry in case_entries:
                if str(case_entry.get("Type") or "").lower() != "directory":
                    continue
                case_folder = str(case_entry.get("Key") or "").strip("/").rsplit("/", 1)[-1]
                if case_folder:
                    folders.append(TRANSCRIPTS_DIR / slug / date_value / case_folder)

    rows: list[dict[str, Any]] = []
    if folders:
        with ThreadPoolExecutor(max_workers=min(8, len(folders))) as executor:
            rows = list(executor.map(_activity_row_from_folder, folders))

    rows.sort(key=lambda r: r.get("savedAt", ""), reverse=True)
    if date:
        rows = [r for r in rows if r.get("savedAt", "").startswith(date)]
    return {"rows": rows, "courts": all_court_slugs}

@app.get("/api/admin/operational-logs/dates")
def admin_operational_logs_dates(
    admin: AuthenticatedAdmin = Depends(_require_admin_user),
) -> dict[str, Any]:
    """Return all dates that have log entries, with their latest severity level."""
    date_latest: dict[str, str] = {}

    for entry in _event_buffer:
        date = entry["time"][:10]   # "2026-07-01"
        date_latest[date] = entry["level"]

    result = [
        {"date": date, "severity": level}
        for date, level in date_latest.items()
    ]

    return {"dates": sorted(result, key=lambda x: x["date"])}

@app.get("/api/admin/operational-logs")
def admin_operational_logs(
    level: str | None = None,
    service: str | None = None,
    date: str | None = None,
    limit: int = 200,
    admin: AuthenticatedAdmin = Depends(_require_admin_user),
) -> dict[str, Any]:
    from collections import Counter

    all_logs = list(_event_buffer)

    # Filter for selected date stats
    stats_logs = [l for l in all_logs if not date or l["time"].startswith(date)]
    level_counts = Counter(l["level"] for l in stats_logs)

    # Filter for table display
    logs = list(reversed(all_logs))
    if date:    logs = [l for l in logs if l["time"].startswith(date)]
    if level:   logs = [l for l in logs if l["level"] == level]
    if service: logs = [l for l in logs if l["service"] == service]

    # Hourly breakdown for selected date (or today if no date)
    target_date = date or _now_colombo().strftime("%Y-%m-%d")
    hourly: dict[int, dict] = {
        h: {"hour": h, "Error": 0, "Warning": 0, "Success": 0, "Info": 0}
        for h in range(24)
    }
    for l in all_logs:
        if l["time"].startswith(target_date):
            try:
                h = int(l["time"][11:13])
                hourly[h][l["level"]] = hourly[h].get(l["level"], 0) + 1
                hourly[h]["latestLevel"] = l["level"]
            except Exception:
                pass

    return {
        "logs": logs[:limit],
        "summary": {
            "total":    len(stats_logs),
            "errors":   level_counts.get("Error", 0),
            "warnings": level_counts.get("Warning", 0),
            "success":  level_counts.get("Success", 0),
            "info":     level_counts.get("Info", 0),
        },
        "hourly": list(hourly.values()),
    }

@app.get("/api/admin/token-prices")
def get_token_prices(
    admin: AuthenticatedAdmin = Depends(_require_admin_user),
) -> dict[str, Any]:
    return {"prices": _token_prices}

@app.put("/api/admin/token-prices")
def update_token_prices(
    payload: dict,
    admin: AuthenticatedAdmin = Depends(_require_admin_user),
) -> dict[str, Any]:
    global _token_prices
    updated = dict(_token_prices)
    for key in _DEFAULT_TOKEN_PRICES:
        if key in payload and isinstance(payload[key], (int, float)) and payload[key] >= 0:
            updated[key] = float(payload[key])
    _token_prices = updated
    try:
        if S3_BUCKET:
            upload_text(
                S3_BUCKET, _TOKEN_PRICES_KEY,
                json.dumps(updated, indent=2),
                content_type="application/json",
                region=AWS_REGION,
            )
    except Exception as exc:
        print(f"[token-prices] Could not save to S3: {exc}")
    return {"prices": _token_prices}

@app.get("/api/health")
def health() -> dict[str, Any]:
    storage = disk_metrics()
    return {
        "ok": storage["status"] != "critical",
        "model": _selected_transcription_model(),
        "quality": _selected_transcription_quality(),
        "inputBucketConfigured": bool(INPUT_BUCKET),
        "outputBucketConfigured": bool(OUTPUT_BUCKET),
        "s3Configured": bool(S3_BUCKET),
        "s3Bucket": S3_BUCKET,
        "storagePrefix": S3_NAMESPACE,
        "transcriptsDir": str(TRANSCRIPTS_DIR),
        "temporaryStorage": storage,
        "audioProcessing": {
            "maximumConcurrentJobs": MAX_CONCURRENT_AUDIO_JOBS,
            "maximumJobsPerCourt":   MAX_JOBS_PER_COURT,
            "activeJobs":            _job_counts()["active"],
            "queuedJobs":            _job_counts()["queued"],
            "vertexJobs":            _job_counts()["vertex"],
            "totalJobs":             _job_counts()["total"],
            "perCourt":              _job_counts()["perCourt"],
        },
    }


@app.get("/api/transcript-dates")
def transcript_dates(current_user: AuthenticatedUser = Depends(_require_authenticated_user)) -> dict[str, Any]:
    # Prefer S3-backed listing if available
    if S3_BUCKET:
        try:
            prefix = f"{S3_NAMESPACE}/{current_user.court_slug}/"
            dates = set()
            entries = _list_s3_directory_entries(prefix)
            for entry in entries:
                if str(entry.get("Type") or "").lower() != "directory":
                    continue
                date_part = str(entry.get("Key") or "").strip("/").rsplit("/", 1)[-1]
                if VALID_DATE.match(date_part):
                    dates.add(date_part)
            if not dates:
                for obj in _list_s3_prefix_objects(prefix):
                    key = str(obj.get("Key") or "")
                    if not key.startswith(prefix):
                        continue
                    suffix = key[len(prefix):].strip("/")
                    date_part = suffix.split("/", 1)[0]
                    if VALID_DATE.match(date_part):
                        dates.add(date_part)
            dates = sorted(dates, reverse=True)
            return {"dates": dates, "defaultDate": _today()}
        except Exception:
            pass

    court_dir = TRANSCRIPTS_DIR / current_user.court_slug
    court_dir.mkdir(parents=True, exist_ok=True)
    dates = sorted([p.name for p in court_dir.iterdir() if p.is_dir() and VALID_DATE.match(p.name)], reverse=True)
    return {"dates": dates, "defaultDate": _today()}


@app.get("/api/transcripts")
def list_transcripts(date: str | None = None, current_user: AuthenticatedUser = Depends(_require_authenticated_user)) -> dict[str, Any]:
    date_value = _validate_date(date)
    # Prefer S3-backed listing when possible
    items = []
    if S3_BUCKET:
        try:
            prefix = f"{S3_NAMESPACE}/{current_user.court_slug}/{date_value}/"
            folder_names = set()
            for entry in _list_s3_directory_entries(prefix):
                if str(entry.get("Type") or "").lower() != "directory":
                    continue
                folder = str(entry.get("Key") or "").strip("/").rsplit("/", 1)[-1]
                if folder:
                    folder_names.add(folder)
            if not folder_names:
                for obj in _list_s3_prefix_objects(prefix):
                    key = str(obj.get("Key") or "")
                    if not key.startswith(prefix):
                        continue
                    suffix = key[len(prefix):].strip("/")
                    folder = suffix.split("/", 1)[0]
                    if folder:
                        folder_names.add(folder)
            folders = [
                TRANSCRIPTS_DIR / current_user.court_slug / date_value / name
                for name in sorted(folder_names, key=lambda s: s.lower())
            ]
            if folders:
                with ThreadPoolExecutor(max_workers=min(8, len(folders))) as executor:
                    items = list(executor.map(lambda folder: _transcript_item(folder, include_content=False), folders))
            items.sort(key=lambda item: item.get("updatedAt", ""), reverse=True)
            return {"date": date_value, "items": items}
        except Exception:
            pass

    date_dir = TRANSCRIPTS_DIR / current_user.court_slug / date_value
    if not date_dir.exists():
        return {"date": date_value, "items": []}
    items = [_transcript_item(folder, include_content=False) for folder in sorted(date_dir.iterdir(), key=lambda p: p.name.lower()) if folder.is_dir()]
    items.sort(key=lambda item: item.get("updatedAt", ""), reverse=True)
    return {"date": date_value, "items": items}


@app.get("/api/transcripts/{item_id}")
def get_transcript(item_id: str, current_user: AuthenticatedUser = Depends(_require_authenticated_user)) -> dict[str, Any]:
    return _transcript_item(_resolve_item_dir(item_id, current_user.court_slug))


@app.put("/api/transcripts/{item_id}/edited")
def save_edited_transcript(
    item_id: str,
    payload: EditedTranscriptRequest,
    current_user: AuthenticatedUser = Depends(_require_authenticated_user),
) -> dict[str, Any]:
    folder = _resolve_item_dir(item_id, current_user.court_slug)
    # Upload edited transcript to S3 and update metadata.
    try:
        key = f"{_storage_prefix_for_folder(folder)}/edited_transcript.txt"
        upload_text(S3_BUCKET, key, payload.content, content_type="text/plain; charset=utf-8", region=AWS_REGION)
        stat_time = _now_colombo().isoformat(timespec="seconds")
        s3_entry = {"name": "edited_transcript.txt", "s3Key": key, "size": len(payload.content.encode("utf-8")), "updatedAt": stat_time}
        meta = _load_metadata(folder)
        files = meta.get("s3Files", [])
        files = [f for f in files if f.get("name") != "edited_transcript.txt"]
        files.append(s3_entry)
        _update_metadata(folder, s3Files=files, finalized=False, finalizedAt="")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not save edited transcript to S3: {exc}") from exc
    return _transcript_item(folder)


@app.post("/api/transcripts/{item_id}/finalize")
def finalize_transcript(
    item_id: str,
    payload: EditedTranscriptRequest,
    current_user: AuthenticatedUser = Depends(_require_authenticated_user),
) -> dict[str, Any]:
    folder = _resolve_item_dir(item_id, current_user.court_slug)
    return _write_finalized_transcript(folder, payload.content)


@app.post("/api/transcripts/{item_id}/post-process")
def generate_verified_outputs(
    item_id: str,
    payload: PostProcessRequest,
    current_user: AuthenticatedUser = Depends(_require_authenticated_user),
) -> dict[str, Any]:
    translate_langs = []
    for lang in payload.translation_languages:
        if lang not in LANGUAGE_NAMES:
            raise HTTPException(status_code=400, detail=f"Unsupported translation language: {lang}")
        if lang not in translate_langs:
            translate_langs.append(lang)
    if not translate_langs and not payload.include_summary:
        raise HTTPException(status_code=400, detail="Select at least one translation language or proceedings.")

    folder = _resolve_item_dir(item_id, current_user.court_slug)
    metadata = _load_metadata(folder)
    if not metadata.get("finalized"):
        raise HTTPException(status_code=400, detail="Verify and finalize the transcript before generating proceedings or translations.")

    job_id = uuid.uuid4().hex
    _set_job(
        job_id,
        id=job_id,
        status="queued",
        message="Verified transcript received by backend",
        created_at=_now_colombo().isoformat(timespec="seconds"),
        translations=translate_langs,
        includeSummary=payload.include_summary,
        court=current_user.court,
        courtSlug=current_user.court_slug,
        uploaderUid=current_user.uid,
    )
    worker = threading.Thread(
        target=_process_post_processing_job,
        args=(job_id, item_id, current_user.court_slug, translate_langs, payload.include_summary),
        daemon=True,
    )
    worker.start()
    return _get_job(job_id)


@app.get("/api/transcripts/{item_id}/file")
def transcript_storage_file(
    item_id: str,
    name: str,
    current_user: AuthenticatedUser = Depends(_require_authenticated_user),
) -> Response:
    folder = _resolve_item_dir(item_id, current_user.court_slug)
    key = _storage_key_for_named_file(folder, name)
    key = _ensure_storage_key_in_court(key, current_user.court_slug)
    data = get_s3_object_bytes(S3_BUCKET, key, region=AWS_REGION)
    if data is None:
        raise HTTPException(status_code=404, detail="File was not found in S3.")
    filename = key.rsplit("/", 1)[-1]
    return Response(
        data,
        media_type=get_mime_type(filename),
        headers={"Content-Disposition": f'inline; filename="{_safe_filename(filename, "file")}"'},
    )

@app.get("/api/transcripts/{item_id}/stream")
def stream_transcript_file(
    item_id: str,
    name: str,
    token: str,
    request: Request,
) -> Response:
    # Verify token from query param (needed since browser audio can't send headers)
    try:
        unverified = _decode_token_payload(token)
        token_project_id = str(unverified.get("aud") or "").strip()
        expected_project_id = FIREBASE_PROJECT_ID or token_project_id
        claims = google_id_token.verify_firebase_token(
            token, google_requests.Request(), audience=expected_project_id
        )
        uid = str(claims.get("uid") or claims.get("user_id") or claims.get("sub") or "").strip()
        if not uid:
            raise HTTPException(status_code=401, detail="Invalid token.")
        profile = _fetch_firestore_user(expected_project_id, uid, token)
        court_slug = _slugify_court_room(str(profile.get("court") or "").strip())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Authentication failed.") from exc

    folder = _resolve_item_dir(item_id, court_slug)
    key = _storage_key_for_named_file(folder, name)
    key = _ensure_storage_key_in_court(key, court_slug)

    mime = get_mime_type(name)
    range_header = request.headers.get("Range")

    chunks, status_code, s3_headers = stream_s3_object_range(
        S3_BUCKET, key, range_header=range_header, region=AWS_REGION
    )

    if chunks is None:
        raise HTTPException(status_code=404, detail="Audio file not found in S3.")

    response_headers = {"Cache-Control": "no-cache", "Accept-Ranges": "bytes"}
    if "Content-Range" in s3_headers:
        response_headers["Content-Range"] = s3_headers["Content-Range"]
    if "Content-Length" in s3_headers:
        response_headers["Content-Length"] = s3_headers["Content-Length"]

    return StreamingResponse(
        chunks,
        status_code=status_code,
        media_type=mime,
        headers=response_headers,
    )

@app.get("/api/transcripts/{item_id}/audio")
def transcript_audio(item_id: str, current_user: AuthenticatedUser = Depends(_require_authenticated_user)):
    folder = _resolve_item_dir(item_id, current_user.court_slug)
    metadata = _load_metadata(folder)
    s3_audio = metadata.get("s3AudioUri")
    if s3_audio and S3_BUCKET:
        key = key_from_storage_uri(s3_audio)
        key = _ensure_storage_key_in_court(key, current_user.court_slug)
        audio_bytes = get_s3_object_bytes(S3_BUCKET, key, region=AWS_REGION)
        if audio_bytes is not None:
            filename = key.rsplit("/", 1)[-1]
            return Response(audio_bytes, media_type=get_mime_type(filename), headers={"Content-Disposition": f'inline; filename="{_safe_filename(filename, "audio")}"'})
    audio_path = _resolve_audio_path(item_id, current_user.court_slug)
    return FileResponse(audio_path, media_type=get_mime_type(str(audio_path)), filename=audio_path.name)


@app.get("/api/transcripts/{item_id}/download/{version}")
def download_transcript(item_id: str, version: str, current_user: AuthenticatedUser = Depends(_require_authenticated_user)) -> Response:
    folder = _resolve_item_dir(item_id, current_user.court_slug)
    item = _transcript_item(folder)
    version = version.lower().strip()
    if version == "original":
        content = item["transcriptText"]
        title = f"Original Transcript - {item['folderName']}"
        version_label = "Original Word Transcript"
    elif version == "edited":
        content = item["editedTranscriptText"]
        title = f"Edited Transcript - {item['folderName']}"
        version_label = "Edited Word Transcript"
        if not content:
            raise HTTPException(status_code=404, detail="No edited transcript has been saved yet.")
    elif version == "final":
        content = item["finalizedTranscriptText"] or item["editedTranscriptText"] or item["transcriptText"]
        title = f"Final Transcript - {item['folderName']}"
        version_label = "Final Verified Word Transcript"
        if not item.get("finalized"):
            raise HTTPException(status_code=404, detail="Transcript has not been finalized yet.")
    elif version == "srt":
            content = item.get("srtText", "")
            if not content:
                raise HTTPException(status_code=404, detail="No SRT captions are available for this transcript.")
            safe_name = _safe_filename(f"{item['date']}_{item['folderName']}.srt", "transcript.srt")
            return Response(
                content,
                media_type="application/x-subrip; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
            )

    elif version == "summary":
        content = item.get("summaryText", "") or ""
        if not content:
            raise HTTPException(status_code=404, detail="No proceeding has been generated yet.")
        title = f"Court Proceeding - {item['folderName']}"
        version_label = "Court Proceeding (Q&A Format)"

    elif version == "translation_combined":
        translations = item.get("translations", [])
        if not translations:
            raise HTTPException(status_code=404, detail="No translations have been generated yet.")
        parts = []
        for t in translations:
            lang = t.get("language", "")
            text = t.get("content", "")
            if text:
                parts.append(f"[{lang.upper()} TRANSLATION]\n\n{text}")
        content = "\n\n".join(parts)
        if not content:
            raise HTTPException(status_code=404, detail="No translations have been generated yet.")
        title = f"Translations - {item['folderName']}"
        version_label = "Translated Transcript (All Languages)"

    else:
        raise HTTPException(status_code=400, detail="Version must be original, edited, final, proceeding or translation_combined.")

    docx = _build_docx(title, item, version_label, content)
    docx_bytes = docx.getvalue()
    safe_name = _safe_filename(f"{item['date']}_{item['folderName']}_{version}.docx", "transcript.docx")
    try:
        s3_key = f"{_storage_prefix(current_user.court_slug, item['date'], item['folderName'])}/downloads/{version}.docx"
        upload_bytes(S3_BUCKET, s3_key, docx_bytes, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", region=AWS_REGION)
    except Exception:
        pass
    # Fallback to streaming DOCX if S3 fails
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}"'}
    return StreamingResponse(
        BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=headers,
    )

@app.get("/api/transcripts/{item_id}/s3-presign")
def s3_presign(item_id: str, name: str, current_user: AuthenticatedUser = Depends(_require_authenticated_user)) -> dict[str, str]:
    folder = _resolve_item_dir(item_id, current_user.court_slug)
    key = _storage_key_for_named_file(folder, name)
    _ensure_storage_key_in_court(key, current_user.court_slug)
    encoded_item = urllib.parse.quote(item_id, safe="")
    encoded_name = urllib.parse.quote(name, safe="")
    return {"url": f"/api/transcripts/{encoded_item}/file?name={encoded_name}"}
    metadata = _load_metadata(folder)

    # Check metadata s3Files first
    for f in metadata.get("s3Files", []):
        if f.get("name") == name and f.get("s3Key"):
            s3_key = _ensure_storage_key_in_court(str(f.get("s3Key")), current_user.court_slug)
            url = presign_s3_url(S3_BUCKET, s3_key, region=AWS_REGION)
            if url:
                return {"url": url}

    # Fallback — build S3 key directly from folder path
    if S3_BUCKET and name:
        try:
            direct_key = f"{_storage_prefix_for_folder(folder)}/{name}"
            direct_key = _ensure_storage_key_in_court(direct_key, current_user.court_slug)
            url = presign_s3_url(S3_BUCKET, direct_key, region=AWS_REGION)
            if url:
                return {"url": url}
        except Exception:
            pass

    raise HTTPException(status_code=404, detail="S3 file not found")

@app.post("/api/uploads")
async def upload_audio(
    files: list[UploadFile] = File(...),
    case_number: str = Form(""),
    hearing_date: str = Form(""),
    current_user: AuthenticatedUser = Depends(_require_authenticated_user),
) -> dict[str, Any]:
    hearing_date = _validate_date(hearing_date)
    case_number = case_number.strip()
    case_folder = _sanitize_case_number(case_number)

    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    expected_bytes = sum(int(getattr(file, "size", 0) or 0) for file in files)
    capacity_ok, _ = has_capacity(expected_bytes)
    if not capacity_ok:
        for file in files:
            await file.close()
        raise HTTPException(status_code=507, detail=error_info("SRV-0001").message)

    job_id = uuid.uuid4().hex
    workspace: Path | None = None
    handed_to_worker = False
    try:
        workspace = create_job_workspace(job_id)
        original_dir = workspace / "original"
        local_paths = []
        audio_file_names = []
        total = 0
        next_capacity_check = 64 * 1024 * 1024

        for i, file in enumerate(files):
            original = _safe_filename(file.filename or "audio", "audio")
            original_ext = Path(original).suffix.lower()
            if original_ext not in SUPPORTED_AUDIO_EXTENSIONS:
                raise HTTPException(status_code=400, detail=f"Unsupported audio type: {original_ext}")

            ts = _now_colombo().strftime('%H-%M-%S')
            suffix = f"_{i + 1}" if len(files) > 1 else ""
            audio_file_name = f"{current_user.court_slug}_{case_folder}_{hearing_date}_{ts}{suffix}{original_ext}"
            local_path = original_dir / audio_file_name

            try:
                with local_path.open("wb") as out:
                    while True:
                        chunk = await file.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_UPLOAD_BYTES:
                            raise HTTPException(status_code=413, detail="Total files too large. Maximum is 2 GB.")
                        if total >= next_capacity_check:
                            capacity_ok, _ = has_capacity(total)
                            if not capacity_ok:
                                raise HTTPException(status_code=507, detail=error_info("SRV-0001").message)
                            next_capacity_check += 64 * 1024 * 1024
                        out.write(chunk)
            finally:
                await file.close()

            local_paths.append(str(local_path))
            audio_file_names.append(audio_file_name)

        _set_job(
        job_id,
        id=job_id,
        status="uploaded_to_backend",
        message=f"{len(files)} audio file(s) received; background processing started",
        created_at=_now_colombo().isoformat(timespec="seconds"),
        fileName=audio_file_names[0],
        fileNames=audio_file_names,
        fileSize=total,
        hearingDate=hearing_date,
        caseNumber=case_number,
        court=current_user.court,
        courtSlug=current_user.court_slug,
        uploaderUid=current_user.uid,
    )

        worker = threading.Thread(
            target=_process_upload_job,
            args=(job_id, local_paths, str(workspace)),
            kwargs={
            "case_number": case_number,
            "case_folder": case_folder,
            "hearing_date": hearing_date,
            "court": current_user.court,
            "court_slug": current_user.court_slug,
            "uploaded_by": current_user.display_name or current_user.username or current_user.email,
            "uploader_uid": current_user.uid,
            "stored_audio_names": audio_file_names,
            "translate_langs": [],
            "include_summary": False,
            },
            daemon=True,
        )
        worker.start()
        handed_to_worker = True
        return _get_job(job_id)
    finally:
        if not handed_to_worker:
            for file in files:
                try:
                    await file.close()
                except Exception:
                    pass
            if workspace is not None:
                try:
                    cleanup_job_workspace(workspace)
                except Exception:
                    pass
            
@app.get("/api/jobs/{job_id}")
def get_upload_job(job_id: str, current_user: AuthenticatedUser = Depends(_require_authenticated_user)) -> dict[str, Any]:
    return _get_authorized_job(job_id, current_user)


# ---------------------------------------------------------------------------
# /api/v1 versioned router
# ---------------------------------------------------------------------------
v1_router = APIRouter(prefix="/api/v1", tags=["v1"])

v1_router.add_api_route("/health", health, methods=["GET"], tags=["System"], summary="Health check")
v1_router.add_api_route("/uploads", upload_audio, methods=["POST"], tags=["Audio Upload"], summary="Upload audio file(s) and start a transcription job")
v1_router.add_api_route("/jobs/{job_id}", get_upload_job, methods=["GET"], tags=["Jobs"], summary="Get transcription job status")
v1_router.add_api_route("/transcript-dates", transcript_dates, methods=["GET"], tags=["Transcripts"], summary="List dates that have transcripts")
v1_router.add_api_route("/transcripts", list_transcripts, methods=["GET"], tags=["Transcripts"], summary="List transcripts for a given date")
v1_router.add_api_route("/transcripts/{item_id}", get_transcript, methods=["GET"], tags=["Transcripts"], summary="Get a single transcript with full content")
v1_router.add_api_route("/transcripts/{item_id}/edited", save_edited_transcript, methods=["PUT"], tags=["Transcripts"], summary="Save an edited transcript")
v1_router.add_api_route("/transcripts/{item_id}/finalize", finalize_transcript, methods=["POST"], tags=["Transcripts"], summary="Finalize a transcript")
v1_router.add_api_route("/transcripts/{item_id}/post-process", generate_verified_outputs, methods=["POST"], tags=["Transcripts"], summary="Generate proceeding and/or translations")
v1_router.add_api_route("/transcripts/{item_id}/audio", transcript_audio, methods=["GET"], tags=["Downloads"], summary="Get audio file bytes")
v1_router.add_api_route("/transcripts/{item_id}/stream", stream_transcript_file, methods=["GET"], tags=["Downloads"], summary="Stream audio with HTTP range support")
v1_router.add_api_route("/transcripts/{item_id}/download/{version}", download_transcript, methods=["GET"], tags=["Downloads"], summary="Download a versioned transcript (original, edited, final, srt, summary, translation_combined)")
v1_router.add_api_route("/transcripts/{item_id}/file", transcript_storage_file, methods=["GET"], tags=["Downloads"], summary="Get a named file from storage for a transcript")

app.include_router(v1_router)
