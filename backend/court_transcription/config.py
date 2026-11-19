from __future__ import annotations

import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

FLASH_MODEL = "gemini-3.6-flash"


PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip().strip('"')
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "").strip()
LOCATION = os.getenv("VERTEX_AI_LOCATION", "us-central1")
INPUT_BUCKET = os.getenv("GCS_INPUT_BUCKET")
OUTPUT_BUCKET = os.getenv("GCS_OUTPUT_BUCKET")
S3_BUCKET = (os.getenv("S3_BUCKET_NAME") or os.getenv("S3_BUCKET") or "").strip().strip('"')
AWS_REGION = os.getenv("AWS_REGION", "").strip().strip('"') or None
S3_NAMESPACE = os.getenv("S3_NAMESPACE", "court-files").strip().strip("/")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", FLASH_MODEL).strip()
BATCH_FALLBACK_MODEL = os.getenv("BATCH_FALLBACK_MODEL", FLASH_MODEL).strip()
BATCH_MODEL_CANDIDATES = [
    model.strip()
    for model in os.getenv("BATCH_MODEL_CANDIDATES", FLASH_MODEL).split(",")
    if model.strip()
]
SPEAKER_ID_MODE = os.getenv("SPEAKER_ID_MODE", "acoustic").strip().lower()
TRANSCRIPTION_QUALITY = os.getenv("TRANSCRIPTION_QUALITY", "accurate").strip().lower()  # fast | accurate
FAST_MODEL = os.getenv("FAST_MODEL", FLASH_MODEL).strip()
ACCURATE_MODEL = os.getenv("ACCURATE_MODEL", FLASH_MODEL).strip()
TRANSCRIPTION_TOP_P = float(os.getenv("TRANSCRIPTION_TOP_P", "0.9"))
TRANSCRIPTION_MAX_OUTPUT_TOKENS = int(os.getenv("TRANSCRIPTION_MAX_OUTPUT_TOKENS", "65535"))
AUDIT_TOP_P = float(os.getenv("AUDIT_TOP_P", str(TRANSCRIPTION_TOP_P)))
AUDIT_MAX_OUTPUT_TOKENS = int(os.getenv("AUDIT_MAX_OUTPUT_TOKENS", str(TRANSCRIPTION_MAX_OUTPUT_TOKENS)))
GCS_UPLOAD_TIMEOUT = int(os.getenv("GCS_UPLOAD_TIMEOUT", "900"))  # seconds
GCS_UPLOAD_CHUNK_SIZE_MB = int(os.getenv("GCS_UPLOAD_CHUNK_SIZE_MB", "16"))
FLAC_SAMPLE_RATE = int(os.getenv("FLAC_SAMPLE_RATE", "16000"))  # Hz
FLAC_CHANNELS = int(os.getenv("FLAC_CHANNELS", "1"))  # 1=mono, 2=stereo
FLAC_COMPRESSION_LEVEL = int(os.getenv("FLAC_COMPRESSION_LEVEL", "5"))  # 0-12
ENHANCE_AUDIO_ON_UPLOAD = os.getenv("ENHANCE_AUDIO_ON_UPLOAD", "true").strip().lower() in ("1", "true", "yes", "y")
CHUNK_AUDIO_ON_UPLOAD = os.getenv("CHUNK_AUDIO_ON_UPLOAD", "true").strip().lower() in ("1", "true", "yes", "y")
AUDIO_CHUNK_SECONDS = int(os.getenv("AUDIO_CHUNK_SECONDS", "300"))  # 5 minutes
MIN_TRAILING_CHUNK_SECONDS = float(os.getenv("MIN_TRAILING_CHUNK_SECONDS", "1.0"))
SKIP_SILENT_CHUNKS = os.getenv("SKIP_SILENT_CHUNKS", "false").strip().lower() in ("1", "true", "yes", "y")
SILENCE_DETECTION_NOISE_DB = int(os.getenv("SILENCE_DETECTION_NOISE_DB", "-35"))
SILENCE_DETECTION_MIN_DURATION = float(os.getenv("SILENCE_DETECTION_MIN_DURATION", "1.0"))
MIN_SPEECH_SECONDS_PER_CHUNK = float(os.getenv("MIN_SPEECH_SECONDS_PER_CHUNK", "4.0"))
MIN_SPEECH_RATIO_PER_CHUNK = float(os.getenv("MIN_SPEECH_RATIO_PER_CHUNK", "0.01"))
REMOVE_REPETITION_LOOPS = os.getenv("REMOVE_REPETITION_LOOPS", "true").strip().lower() in ("1", "true", "yes", "y")
ENABLE_TRANSCRIPT_COVERAGE_AUDIT = os.getenv("ENABLE_TRANSCRIPT_COVERAGE_AUDIT", "false").strip().lower() in ("1", "true", "yes", "y")
ENABLE_REPETITION_REPAIR = os.getenv("ENABLE_REPETITION_REPAIR", "true").strip().lower() in ("1", "true", "yes", "y")
REPETITION_REPAIR_MIN_REMOVED = int(os.getenv("REPETITION_REPAIR_MIN_REMOVED", "3"))
TIMEOUT = int(os.getenv("TRANSCRIPTION_TIMEOUT", 7200))
CREATE_MPEG = os.getenv("CREATE_MPEG", "false").strip().lower() in ("1", "true", "yes", "y")

# Local temporary-storage safety. Permanent transcripts/audio remain in S3/GCS.
_default_temp_root = "/var/tmp/court-transcription" if os.name != "nt" else str(Path(tempfile.gettempdir()) / "court-transcription")
TEMP_STORAGE_ROOT = Path(os.getenv("TEMP_STORAGE_ROOT", _default_temp_root)).expanduser()
TEMP_STORAGE_STALE_HOURS = max(1, int(os.getenv("TEMP_STORAGE_STALE_HOURS", "24")))
TEMP_STORAGE_MIN_FREE_GB = max(1.0, float(os.getenv("TEMP_STORAGE_MIN_FREE_GB", "5")))
TEMP_STORAGE_WARNING_FREE_GB = max(TEMP_STORAGE_MIN_FREE_GB, float(os.getenv("TEMP_STORAGE_WARNING_FREE_GB", "8")))
TEMP_STORAGE_RESERVE_MULTIPLIER = max(1.0, float(os.getenv("TEMP_STORAGE_RESERVE_MULTIPLIER", "3")))

# Audio job concurrency control.
MAX_CONCURRENT_AUDIO_JOBS = max(1, int(os.getenv("MAX_CONCURRENT_AUDIO_JOBS", "8")))
MAX_JOBS_PER_COURT    = max(1, int(os.getenv("MAX_JOBS_PER_COURT", "2")))
MAX_QUEUED_AUDIO_JOBS = max(0, int(os.getenv("MAX_QUEUED_AUDIO_JOBS", "50")))