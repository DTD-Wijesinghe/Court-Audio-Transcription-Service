from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from google.cloud import storage


from .config import (
    AUDIO_CHUNK_SECONDS,
    FLAC_CHANNELS,
    FLAC_COMPRESSION_LEVEL,
    FLAC_SAMPLE_RATE,
    GCS_UPLOAD_CHUNK_SIZE_MB,
    GCS_UPLOAD_TIMEOUT,
    MIN_SPEECH_RATIO_PER_CHUNK,
    MIN_SPEECH_SECONDS_PER_CHUNK,
    MIN_TRAILING_CHUNK_SECONDS,
    SILENCE_DETECTION_MIN_DURATION,
    SILENCE_DETECTION_NOISE_DB,
    SKIP_SILENT_CHUNKS,
)
from .chunking import SKIPPED_AUDIO_SCHEME

SUPPORTED_AUDIO_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg", ".mp4", ".m4a", ".opus")
LIST_AUDIO_EXTENSIONS = SUPPORTED_AUDIO_EXTENSIONS


@dataclass(frozen=True)
class LocalAudioChunk:
    path: str
    start_seconds: int
    end_seconds: int


def _get_ffmpeg_exe() -> str:
    """Return an ffmpeg executable path (bundled via imageio-ffmpeg)."""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except Exception as e:
        raise RuntimeError(
            "ffmpeg is required to convert audio to FLAC. "
            "Install dependency: pip install imageio-ffmpeg"
        ) from e


def _audio_settings() -> tuple[int, int, int]:
    """Return validated FLAC sample rate, channel count, and compression level."""
    sr = 16000
    ch = 1
    lvl = 5
    try:
        sr = int(FLAC_SAMPLE_RATE) if FLAC_SAMPLE_RATE else 16000
    except Exception:
        sr = 16000
    try:
        ch = int(FLAC_CHANNELS) if FLAC_CHANNELS else 1
    except Exception:
        ch = 1
    try:
        lvl = int(FLAC_COMPRESSION_LEVEL) if FLAC_COMPRESSION_LEVEL else 5
    except Exception:
        lvl = 5

    if sr < 8000:
        sr = 8000
    if sr > 48000:
        sr = 48000
    if ch not in (1, 2):
        ch = 1
    if lvl < 0:
        lvl = 0
    if lvl > 12:
        lvl = 12

    return sr, ch, lvl


def _run_ffmpeg(cmd: list[str], action: str) -> None:
    import subprocess

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"ffmpeg {action} failed: {err}")


def _probe_audio_duration_seconds(input_path: str) -> float | None:
    """Read audio duration from ffmpeg metadata output."""
    import subprocess

    ffmpeg = _get_ffmpeg_exe()
    proc = subprocess.run([ffmpeg, "-i", input_path], capture_output=True, text=True)
    text = f"{proc.stderr}\n{proc.stdout}"
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not match:
        return None

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return (hours * 3600) + (minutes * 60) + seconds


def _speech_seconds_in_audio(input_path: str, duration_seconds: float) -> float | None:
    """Estimate non-silent seconds using ffmpeg silencedetect output."""
    import subprocess

    if duration_seconds <= 0:
        return None

    ffmpeg = _get_ffmpeg_exe()
    noise_db = int(SILENCE_DETECTION_NOISE_DB)
    min_duration = max(0.1, float(SILENCE_DETECTION_MIN_DURATION))
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            input_path,
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_duration}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    text = f"{proc.stderr}\n{proc.stdout}"
    silence_total = 0.0
    open_start: float | None = None

    for line in text.splitlines():
        start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
        if start_match:
            try:
                open_start = float(start_match.group(1))
            except ValueError:
                open_start = None
            continue

        end_match = re.search(r"silence_end:\s*([0-9.]+).*?silence_duration:\s*([0-9.]+)", line)
        if end_match:
            try:
                silence_total += float(end_match.group(2))
            except ValueError:
                if open_start is not None:
                    try:
                        silence_total += max(0.0, float(end_match.group(1)) - open_start)
                    except ValueError:
                        pass
            open_start = None

    if open_start is not None:
        silence_total += max(0.0, duration_seconds - open_start)

    silence_total = min(max(0.0, silence_total), duration_seconds)
    return max(0.0, duration_seconds - silence_total)


def _should_skip_low_speech_chunk(chunk: LocalAudioChunk) -> tuple[bool, float]:
    if not SKIP_SILENT_CHUNKS:
        return False, 0.0

    duration = max(0.0, float(chunk.end_seconds - chunk.start_seconds))
    if duration <= 0:
        return False, 0.0

    speech_seconds = _speech_seconds_in_audio(chunk.path, duration)
    if speech_seconds is None:
        return False, 0.0

    speech_ratio = speech_seconds / duration if duration else 0.0
    should_skip = (
        speech_seconds < float(MIN_SPEECH_SECONDS_PER_CHUNK)
        or speech_ratio < float(MIN_SPEECH_RATIO_PER_CHUNK)
    )
    return should_skip, speech_seconds


def _convert_to_flac(input_path: str, output_path: str) -> None:
    """Convert any supported audio to FLAC using ffmpeg."""
    ffmpeg = _get_ffmpeg_exe()
    sr, ch, lvl = _audio_settings()

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        input_path,
        "-vn",
        "-ac",
        str(ch),
        "-ar",
        str(sr),
        "-c:a",
        "flac",
        "-compression_level",
        str(lvl),
        output_path,
    ]

    _run_ffmpeg(cmd, "convert")


def _enhance_to_flac(input_path: str, output_path: str) -> None:
    """Create speech-focused FLAC audio for transcription."""
    ffmpeg = _get_ffmpeg_exe()
    sr, ch, lvl = _audio_settings()
    speech_filter = "highpass=f=100,lowpass=f=7500,afftdn=nf=-30:nr=0.8,dynaudnorm=f=100:g=20,loudnorm=I=-14:TP=-1.5:LRA=7"
    fallback_filter = "highpass=f=80,lowpass=f=8000,loudnorm=I=-16:TP=-1.5:LRA=11"

    def build_cmd(filter_chain: str) -> list[str]:
        return [
            ffmpeg,
            "-y",
            "-i",
            input_path,
            "-vn",
            "-af",
            filter_chain,
            "-ac",
            str(ch),
            "-ar",
            str(sr),
            "-c:a",
            "flac",
            "-compression_level",
            str(lvl),
            output_path,
        ]

    try:
        _run_ffmpeg(build_cmd(speech_filter), "enhance")
    except RuntimeError:
        _run_ffmpeg(build_cmd(fallback_filter), "enhance")


def _split_flac(input_path: str, output_dir: str, chunk_seconds: int | None = None) -> list[LocalAudioChunk]:
    """Split FLAC audio into deterministic fixed-duration chunk files."""
    from pathlib import Path

    ffmpeg = _get_ffmpeg_exe()
    sr, ch, lvl = _audio_settings()
    chunk_seconds = int(chunk_seconds or AUDIO_CHUNK_SECONDS or 300)
    if chunk_seconds < 60:
        chunk_seconds = 300

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    total_duration = _probe_audio_duration_seconds(input_path)
    if total_duration is None or total_duration <= 0:
        raise RuntimeError("Could not determine audio duration before splitting into chunks.")

    expected_chunks = max(1, math.ceil(max(0.0, total_duration - 0.25) / chunk_seconds))
    print(f"  Enhanced duration: {total_duration / 60:.2f} minutes; expected chunks: {expected_chunks}")

    OVERLAP_SECONDS = 30 
    chunks: list[LocalAudioChunk] = []
    for zero_index in range(expected_chunks):
            overlap = OVERLAP_SECONDS if zero_index > 0 else 0
            content_start = zero_index * chunk_seconds
            actual_start = max(0.0, content_start - overlap)
            end_offset = min((zero_index + 1) * chunk_seconds, total_duration)
            content_duration = end_offset - content_start
            segment_duration = end_offset - actual_start   # longer by overlap

            if content_duration <= 0:
                continue
            if chunks and content_duration < MIN_TRAILING_CHUNK_SECONDS:
                continue

            output_path = out_dir / f"chunk_{zero_index + 1:03d}.flac"
            cmd = [
                ffmpeg,
                "-y",
                "-ss",
                f"{actual_start:.3f}",      # starts earlier by overlap seconds
                "-t",
                f"{segment_duration:.3f}",  # duration includes the overlap
                "-i",
                input_path,
                "-vn",
                "-map_metadata",
                "-1",
                "-ac",
                str(ch),
                "-ar",
                str(sr),
                "-c:a",
                "flac",
                "-compression_level",
                str(lvl),
                str(output_path),
            ]
            _run_ffmpeg(cmd, "split")

            # Store content boundaries (not actual_start) so filename timestamps
            # reflect the content this chunk is responsible for, not the overlap prefix.
            start_seconds = int(round(content_start))
            end_seconds = int(round(end_offset))
            if end_seconds <= start_seconds:
                end_seconds = start_seconds + 1

            chunks.append(
                LocalAudioChunk(
                    path=str(output_path),
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                )
            )

    return chunks


def get_mime_type(uri: str) -> str:
    ext = uri.lower().rsplit(".", 1)[-1]
    return {
        "mp3": "audio/mp3",
        "wav": "audio/wav",
        "flac": "audio/flac",
        "ogg": "audio/ogg",
        "mp4": "audio/mp4",
        "m4a": "audio/mp4",
        "opus": "audio/ogg",
    }.get(ext, "audio/mp3")


def list_audio_files_in_bucket(bucket_name: str, prefix: str | None = None) -> list:
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    list_prefix = (prefix or "").strip().lstrip("/")
    blobs = bucket.list_blobs(prefix=list_prefix or None)

    supported = LIST_AUDIO_EXTENSIONS
    if list_prefix:
        print(f"  Filtering GCS path: gs://{bucket_name}/{list_prefix}")
    uris = []

    for blob in blobs:
        if blob.name.lower().endswith(supported):
            uri = f"gs://{bucket_name}/{blob.name}"
            uris.append(uri)
            print(f"  Found: {uri}")

    return uris


def upload_to_gcs(local_path: str, bucket_name: str, gcs_blob_name: str) -> str:
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(gcs_blob_name)

    # This is only the HTTP transfer chunk size used by GCS resumable uploads.
    # It does not split the audio for transcription; Gemini receives one GCS
    # file URI per batch request and Google manages the processing load.
    try:
        chunk_mb = int(GCS_UPLOAD_CHUNK_SIZE_MB) if GCS_UPLOAD_CHUNK_SIZE_MB else 16
    except Exception:
        chunk_mb = 16
    if chunk_mb <= 0:
        chunk_mb = 16
    blob.chunk_size = max(256 * 1024, chunk_mb * 1024 * 1024)

    timeout_s = 900
    try:
        timeout_s = int(GCS_UPLOAD_TIMEOUT) if GCS_UPLOAD_TIMEOUT else 900
    except Exception:
        timeout_s = 900

    # Use resumable upload path under the hood; timeout applies per request.
    blob.upload_from_filename(local_path, timeout=timeout_s)

    uri = f"gs://{bucket_name}/{gcs_blob_name}"
    print(f"  OK Uploaded to GCS: {uri}")
    return uri


def download_and_parse_results(output_gcs_prefix: str) -> dict:
    storage_client = storage.Client()

    path = output_gcs_prefix.replace("gs://", "")
    bucket_name = path.split("/")[0]
    prefix = "/".join(path.split("/")[1:])

    bucket = storage_client.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix=prefix))

    results = {}

    for blob in blobs:
        if not blob.name.endswith(".jsonl"):
            continue

        print(f"  📥 Reading results: gs://{bucket_name}/{blob.name}")
        content = blob.download_as_text(encoding="utf-8")

        for line in content.strip().split("\n"):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                request_id = data.get("id", "")
                status = data.get("status", "")

                if status and status != "OK" and not data.get("response"):
                    print(f"    ⚠️  Request {request_id} failed: {status}")
                    continue

                response = data.get("response", {})
                candidates = response.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        text = parts[0].get("text", "")
                        results[request_id] = text

                usage = response.get("usageMetadata", {})
                if usage:
                    prompt_details = usage.get("promptTokensDetails", []) or []
                    candidate_details = usage.get("candidatesTokensDetails", []) or []

                    audio_input = sum(d.get("tokenCount", 0) for d in prompt_details if d.get("modality") == "AUDIO")
                    text_input = sum(d.get("tokenCount", 0) for d in prompt_details if d.get("modality") == "TEXT")
                    text_output = sum(d.get("tokenCount", 0) for d in candidate_details if d.get("modality") == "TEXT")

                    # Thinking tokens and Cached prompt tokens
                    thinking_tokens = (
                        sum(d.get("tokenCount", 0) for d in candidate_details if d.get("modality") == "THINKING")
                        or usage.get("thoughtsTokenCount", 0)
                    )
                    cached_tokens = usage.get("cachedContentTokenCount", 0)

                    results[f"{request_id}__usage"] = {
                        "audioInput": audio_input,
                        "textInput": text_input,
                        "textOutput": text_output,
                        "thinkingTokens": thinking_tokens,
                        "cachedTokens": cached_tokens,
                        "total": usage.get("totalTokenCount", 0),
                    }

            except json.JSONDecodeError as e:
                print(f"    ⚠️  Could not parse result line: {e}")

    print(f"\n  ✅ Parsed {len(results)} result(s) from GCS")
    return results


def upload_audio_file(
    local_path: str,
    bucket_name: str,
    prefix: str = "uploads/manual",
    convert_to_flac: bool = False,
    enhance_audio: bool = False,
    chunk_audio: bool = False,
    progress_callback: Callable[[str, str], None] | None = None,
    work_dir: str | None = None,
) -> list[str]:
    """Upload one local audio file to GCS.

    Returns a single-item list so the caller can keep its existing multi-file flow.
    """
    from datetime import datetime
    from pathlib import Path
    import tempfile
    import shutil

    p = Path(str(local_path).strip().strip('"')).expanduser()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")

    ext = p.suffix.lower()
    if ext not in SUPPORTED_AUDIO_EXTENSIONS:
        raise ValueError(f"Unsupported audio type: {ext}. Supported: {SUPPORTED_AUDIO_EXTENSIONS}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_base = p.stem.replace(" ", "_")

    if chunk_audio:
        enhance_audio = True
        convert_to_flac = True

    if not convert_to_flac and not enhance_audio:
        if progress_callback:
            progress_callback("uploading_chunks_to_gcs", f"Uploading {p.name} to Google Cloud")
        duration_seconds = _probe_audio_duration_seconds(str(p)) or 0.0
        upload_name = f"{upload_base}{ext}"
        blob_name = f"{prefix}/{ts}_{upload_name}"
        uri = upload_to_gcs(str(p), bucket_name, blob_name)
        return [uri], duration_seconds

    owns_work_dir = not work_dir
    tmp_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="court-audio-"))
    processed_dir = tmp_dir / "processed" if work_dir else tmp_dir
    chunks_root = tmp_dir / "chunks" if work_dir else tmp_dir
    processed_dir.mkdir(parents=True, exist_ok=True)
    chunks_root.mkdir(parents=True, exist_ok=True)
    flac_path = processed_dir / f"{ts}_{upload_base}.flac"
    processing_label = "enhanced" if enhance_audio else "converted"

    if ext == ".flac" and not enhance_audio:
        # Keep original if already FLAC.
        flac_path = p
    elif enhance_audio:
        if progress_callback:
            progress_callback("enhancing_audio", f"Enhancing {p.name} for speech clarity")
        print(f"  Enhancing audio for speech clarity: {p.name} -> {flac_path.name}")
        try:
            _enhance_to_flac(str(p), str(flac_path))
        except Exception:
            flac_path.unlink(missing_ok=True)
            if owns_work_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
    else:
        if progress_callback:
            progress_callback("preparing_audio", f"Converting {p.name} to FLAC")
        print(f"  Converting to FLAC: {p.name} -> {flac_path.name}")
        try:
            _convert_to_flac(str(p), str(flac_path))
        except Exception:
            flac_path.unlink(missing_ok=True)
            if owns_work_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    owns_flac = flac_path != p
    try:
        duration_seconds = _probe_audio_duration_seconds(str(flac_path)) or 0.0

        if chunk_audio:
            chunk_dir = chunks_root / f"{ts}_{upload_base}"
            if progress_callback:
                progress_callback("chunking_audio", f"Splitting {p.name} into transcript chunks")
            print(f"  Splitting {processing_label} audio into {AUDIO_CHUNK_SECONDS // 60}-minute chunks...")
            chunks = _split_flac(str(flac_path), str(chunk_dir), AUDIO_CHUNK_SECONDS)
            if not chunks:
                raise RuntimeError("No audio chunks were created.")

            uploaded: list[str] = []
            total = len(chunks)
            chunk_folder = f"{prefix}/{ts}_{upload_base}"
            for index, chunk in enumerate(chunks, start=1):
                chunk_name = f"chunk_{index:03d}_of_{total:03d}_{chunk.start_seconds:06d}-{chunk.end_seconds:06d}.flac"
                blob_name = f"{chunk_folder}/{chunk_name}"
                try:
                    should_skip, speech_seconds = _should_skip_low_speech_chunk(chunk)
                    if should_skip:
                        print(
                            f"  Skipping chunk {index}/{total}: no clear speech detected "
                            f"({speech_seconds:.1f}s speech): {chunk_name}"
                        )
                        uploaded.append(f"{SKIPPED_AUDIO_SCHEME}{bucket_name}/{blob_name}")
                    else:
                        if progress_callback:
                            progress_callback("uploading_chunks_to_gcs", f"Uploading chunk {index}/{total} to Google Cloud")
                        print(f"  Uploading chunk {index}/{total}: {chunk_name}")
                        uploaded.append(upload_to_gcs(chunk.path, bucket_name, blob_name))
                finally:
                    # A chunk is no longer needed locally once uploaded or deliberately skipped.
                    Path(chunk.path).unlink(missing_ok=True)
            try:
                chunk_dir.rmdir()
            except OSError:
                pass
            return uploaded, duration_seconds

        upload_name = f"{upload_base}.flac"
        blob_name = f"{prefix}/{ts}_{upload_name}"
        if progress_callback:
            progress_callback("uploading_chunks_to_gcs", f"Uploading prepared audio for {p.name} to Google Cloud")
        uri = upload_to_gcs(str(flac_path), bucket_name, blob_name)
        return [uri], duration_seconds
    finally:
        if owns_flac:
            flac_path.unlink(missing_ok=True)
        if owns_work_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
