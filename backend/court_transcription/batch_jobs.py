from __future__ import annotations

import json
import time

from google.genai.types import CreateBatchJobConfig, HttpOptions, JobState

from .config import (
    AUDIT_MAX_OUTPUT_TOKENS,
    AUDIT_TOP_P, 
    BATCH_FALLBACK_MODEL,
    BATCH_MODEL_CANDIDATES,
    GEMINI_MODEL,
    LOCATION,
    PROJECT_ID,
    SPEAKER_ID_MODE,
    TRANSCRIPTION_MAX_OUTPUT_TOKENS,
    TRANSCRIPTION_TOP_P,
    TRANSCRIPTION_QUALITY,
)
from .gcs_utils import get_mime_type
from .chunking import inspect_audio_uri, is_skipped_audio_uri
from .prompts import (
    SUMMARY_PROMPT,
    TRANSCRIPTION_SYSTEM_INSTRUCTION,
    TRANSLATION_PROMPT,
    build_transcript_coverage_audit_prompt,
    build_transcript_repetition_repair_prompt,
    build_transcription_prompt,
)


def _literal_system_instruction() -> dict:
    return {"parts": [{"text": TRANSCRIPTION_SYSTEM_INSTRUCTION.strip()}]}


def _deterministic_audio_generation_config(
    max_output_tokens: int,
    top_p: float | None = None,
) -> dict:
    return {
        "temperature": 0.0,
        "topP": top_p if top_p is not None else TRANSCRIPTION_TOP_P,
        "maxOutputTokens": max_output_tokens,
    }


def is_frequency_penalty_validation_error(exc: Exception) -> bool:
    message = str(getattr(exc, "message", "")) or str(exc)
    message = message.lower()
    return (
        "frequencypenalty" in message
        or "frequency_penalty" in message
        or "frequency penalty" in message
    ) and ("invalid" in message or "unknown" in message or "unsupported" in message)


def _normalize_vertex_model_name(model: str) -> str:
    """
    Vertex AI publisher models are resource names like:
      publishers/google/models/gemini-2.0-flash-001

    The batches API can require the full resource name even when other calls
    accept the short model id (e.g. "gemini-2.0-flash-001").
    """
    model = (model or "").strip()
    if not model:
        return model
    if "/" in model:
        return model
    return f"publishers/google/models/{model}"


def build_transcription_jsonl(
    audio_uris: list,
    jsonl_local_path: str,
    quality: str | None = None,
    index_offset: int = 0,
    previous_transcripts: dict[str, str] | None = None,  # ← add this
) -> int:
    quality = (quality or TRANSCRIPTION_QUALITY or "fast").strip().lower()
    system_instruction = _literal_system_instruction()
    gen_config = _deterministic_audio_generation_config(
        TRANSCRIPTION_MAX_OUTPUT_TOKENS,
        top_p=TRANSCRIPTION_TOP_P,
    )

    # Build parent URI → last N lines map from previous results
    context_map: dict[str, str] = {}
    if previous_transcripts:
        for key, text in previous_transcripts.items():
            if text and text.strip():
                lines = [l for l in text.strip().splitlines() if l.strip()]
                context_map[key] = "\n".join(lines[-8:])  # last 8 lines

    entries_written = 0
    prev_context = ""  # carries context from previous URI in sequence

    with open(jsonl_local_path, "w", encoding="utf-8") as f:
        for local_idx, uri in enumerate(audio_uris):
            if is_skipped_audio_uri(uri):
                prev_context = ""
                continue
            idx = index_offset + local_idx
            audio_item = inspect_audio_uri(uri, idx)
            mime = get_mime_type(uri)
            filename_stem = audio_item.filename_stem
            file_part = {"fileData": {"fileUri": uri, "mimeType": mime}}
            prompt_text = build_transcription_prompt(
                SPEAKER_ID_MODE,
                quality,
                chunked=audio_item.is_chunk,
            ).strip()

            # Inject previous context if available
            if prev_context:
                context_injection = (
                    "\n\nCONTEXT FROM PREVIOUS AUDIO SEGMENT "
                    "(do NOT re-transcribe — use only to maintain "
                    "speaker label consistency and sentence continuity):\n"
                    f"{prev_context}\n"
                    "--- END OF CONTEXT. Begin transcribing the new audio now. ---\n"
                )
                prompt_text = prompt_text + context_injection

            request_id = f"{idx}|{filename_stem}|transcript"
            entry = {
                "id": request_id,
                "request": {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [file_part, {"text": prompt_text}],
                        }
                    ],
                    "systemInstruction": system_instruction,
                    "generationConfig": gen_config,
                },
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            entries_written += 1

            # Store this URI's existing transcript as context for the next one
            existing_key = f"{idx}|{filename_stem}|transcript"
            if previous_transcripts and existing_key in context_map:
                prev_context = context_map[existing_key]
            # For first pass (no previous_transcripts), context builds up empty
            # and will be used in repair/audit passes

    print(f"  OK Transcription batch input created: {jsonl_local_path}")
    return entries_written


def build_transcript_audit_jsonl(
    audio_uris: list,
    transcripts: dict,
    jsonl_local_path: str,
    quality: str | None = None,
) -> int:
    quality = (quality or TRANSCRIPTION_QUALITY or "accurate").strip().lower()
    system_instruction = _literal_system_instruction()
    gen_config = _deterministic_audio_generation_config(AUDIT_MAX_OUTPUT_TOKENS, top_p=AUDIT_TOP_P)
    entries_written = 0

    with open(jsonl_local_path, "w", encoding="utf-8") as f:
        for idx, uri in enumerate(audio_uris):
            if is_skipped_audio_uri(uri):
                continue
            mime = get_mime_type(uri)
            filename_stem = uri.split("/")[-1].rsplit(".", 1)[0]
            transcript_key = f"{idx}|{filename_stem}|transcript"
            draft_transcript = (transcripts.get(transcript_key, "") or "").strip()

            if not draft_transcript:
                print(f"  No draft transcript found for {filename_stem} - skipping coverage audit")
                continue

            file_part = {"fileData": {"fileUri": uri, "mimeType": mime}}
            prompt_text = build_transcript_coverage_audit_prompt(draft_transcript)
            entry = {
                "id": transcript_key,
                "request": {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [file_part, {"text": prompt_text}],
                        }
                    ],
                    "systemInstruction": system_instruction,
                    "generationConfig": gen_config,
                },
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            entries_written += 1

    if entries_written:
        print(f"  OK Transcript coverage audit batch input created: {jsonl_local_path}")
    else:
        print("  No transcript coverage audit entries were created")
    return entries_written


def build_transcript_repair_jsonl(
    repair_items: list[tuple[int, str, str]],
    jsonl_local_path: str,
) -> int:
    system_instruction = _literal_system_instruction()
    gen_config = _deterministic_audio_generation_config(AUDIT_MAX_OUTPUT_TOKENS, top_p=AUDIT_TOP_P)
    entries_written = 0

    with open(jsonl_local_path, "w", encoding="utf-8") as f:
        for idx, uri, draft_transcript in repair_items:
            if is_skipped_audio_uri(uri):
                continue
            draft = (draft_transcript or "").strip()
            if not draft:
                continue

            mime = get_mime_type(uri)
            filename_stem = uri.split("/")[-1].rsplit(".", 1)[0]
            request_id = f"{idx}|{filename_stem}|transcript"
            file_part = {"fileData": {"fileUri": uri, "mimeType": mime}}
            prompt_text = build_transcript_repetition_repair_prompt(draft)
            entry = {
                "id": request_id,
                "request": {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [file_part, {"text": prompt_text}],
                        }
                    ],
                    "systemInstruction": system_instruction,
                    "generationConfig": gen_config,
                },
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            entries_written += 1

    if entries_written:
        print(f"  OK Transcript repetition repair batch input created: {jsonl_local_path}")
    else:
        print("  No transcript repetition repair entries were created")
    return entries_written


def build_text_jsonl(
    audio_uris: list,
    transcripts: dict,
    translate_langs: list,
    include_summary: bool,
    jsonl_local_path: str,
):
    with open(jsonl_local_path, "w", encoding="utf-8") as f:
        for idx, uri in enumerate(audio_uris):
            filename_stem = uri.split("/")[-1].rsplit(".", 1)[0]
            transcript_key = f"{idx}|{filename_stem}|transcript"
            transcript_text = transcripts.get(transcript_key, "")
            gen_config = {"temperature": 0.1, "maxOutputTokens": 65535}

            if not transcript_text:
                print(f"  ⚠️  No transcript found for {filename_stem} — skipping text jobs")
                continue

            for lang in translate_langs:
                request_id = f"{idx}|{filename_stem}|translate_{lang}"
                prompt = TRANSLATION_PROMPT[lang].strip()
                full_text = f"{prompt}\n\n{transcript_text}"
                entry = {
                    "id": request_id,
                    "request": {
                        "contents": [{
                            "role": "user",
                            "parts": [{"text": full_text}]
                        }],
                        "generationConfig": gen_config
                    }
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

            if include_summary:
                request_id = f"{idx}|{filename_stem}|summary"
                full_text = f"{SUMMARY_PROMPT.strip()}\n\n{transcript_text}"
                entry = {
                    "id": request_id,
                    "request": {
                        "contents": [{
                            "role": "user",
                            "parts": [{"text": full_text}]
                        }],
                        "generationConfig": gen_config
                    }
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"  ✅ Translation/summary batch input created: {jsonl_local_path}")


def submit_batch_job(input_gcs_uri: str, output_gcs_prefix: str, model_override: str | None = None):
    from google import genai
    from google.genai import errors as genai_errors

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        # v1beta1 is required for model listing in this project/region and has
        # the most complete publisher-model surface.
        http_options=HttpOptions(api_version="v1beta1"),
    )

    requested_model = (model_override or GEMINI_MODEL or "").strip()

    print("\n  Submitting batch job to Vertex AI...")
    print(f"  Model  : {requested_model}")
    print(f"  Input  : {input_gcs_uri}")
    print(f"  Output : {output_gcs_prefix}")

    model_candidates: list[str] = []

    def add_candidate(model_name: str) -> None:
        model_name = (model_name or "").strip()
        if not model_name:
            return
        if model_name not in model_candidates:
            model_candidates.append(model_name)
        normalized = _normalize_vertex_model_name(model_name)
        if normalized and normalized not in model_candidates:
            model_candidates.append(normalized)

    if model_override:
        # API requests choose their preferred model intentionally, then use the
        # configured list as a compatibility fallback for regional batch support.
        add_candidate(requested_model)
        for m in BATCH_MODEL_CANDIDATES:
            add_candidate(m)
    elif BATCH_MODEL_CANDIDATES:
        for m in BATCH_MODEL_CANDIDATES:
            add_candidate(m)
    else:
        add_candidate(requested_model)

    # Fallback model: used when a configured default isn't supported by batches.
    add_candidate(BATCH_FALLBACK_MODEL or "gemini-2.5-flash")

    job = None
    used_model: str | None = None
    last_err: Exception | None = None

    for m in model_candidates or [requested_model]:
        try:
            job = client.batches.create(
                model=m,
                src=input_gcs_uri,
                config=CreateBatchJobConfig(dest=output_gcs_prefix),
            )
            used_model = m
            break
        except genai_errors.ClientError as e:
            last_err = e
            msg = str(getattr(e, "message", "")) or str(e)
            if "does not exist" in msg.lower() or "not_found" in msg.lower():
                continue
            raise

    if job is None:
        print("\n  Failed to submit batch job with the configured model(s).")
        # Best-effort hint: list Gemini publisher models visible in this region.
        try:
            import itertools

            gemini_models: list[str] = []
            for model_obj in itertools.islice(client.models.list(), 5000):
                name = getattr(model_obj, "name", "")
                if "gemini" in name.lower():
                    gemini_models.append(name)
            if gemini_models:
                print("  Gemini models available in this project/region include:")
                for name in gemini_models[:30]:
                    print(f"   - {name}")
        except Exception:
            pass

        if last_err is not None:
            raise last_err
        raise RuntimeError("Batch job submission failed (unknown error).")

    if used_model and requested_model and used_model != requested_model:
        print(f"  Using model: {used_model} (requested: {requested_model})")

    print("\n  OK Job submitted!")
    print(f"  Job name  : {job.name}")
    print(f"  Job state : {job.state}")

    return job, client



def wait_for_batch_job(job, client) -> bool:
    print("\n  ⏳ Waiting for Google to process all files...")
    print("     (Checking every 30 seconds — this may take a while for long audio)\n")

    start_time = time.time()

    while job.state not in (JobState.JOB_STATE_SUCCEEDED, JobState.JOB_STATE_FAILED, JobState.JOB_STATE_CANCELLED):
        elapsed = int(time.time() - start_time)
        minutes = elapsed // 60
        seconds = elapsed % 60
        print(f"     [{minutes:02d}:{seconds:02d}] Status: {job.state.name} — still processing...")
        time.sleep(30)

        for attempt in range(5):
            try:
                job = client.batches.get(name=job.name)
                break
            except Exception as e:
                if attempt < 4:
                    wait = (attempt + 1) * 10
                    print(f"     ⚠️  Network error polling status (attempt {attempt+1}/5) — retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"     ❌ Failed to get job status after 5 attempts: {e}")
                    print(f"     💡 Job name: {job.name}")
                    raise

    elapsed = int(time.time() - start_time)
    minutes = elapsed // 60
    seconds = elapsed % 60

    if job.state.name == "JOB_STATE_SUCCEEDED":
        print(f"\n  ✅ All done! Completed in {minutes}m {seconds}s")
        return True, job

    print(f"\n  ❌ Job failed after {minutes}m {seconds}s")
    print(f"  Error: {job.error}")
    return False, job

def build_proceeding_chunks_jsonl(
    transcript_chunks: list[str],
    jsonl_local_path: str,
) -> int:
    """
    One JSONL entry per transcript chunk.
    Vertex AI processes all chunks in parallel in one batch job.
    """
    from .prompts import SUMMARY_PROMPT

    gen_config = {"temperature": 0.1, "maxOutputTokens": 65535}
    entries_written = 0

    with open(jsonl_local_path, "w", encoding="utf-8") as f:
        for idx, chunk_text in enumerate(transcript_chunks):
            entry = {
                "id": f"proceeding_chunk_{idx}",
                "request": {
                    "contents": [{
                        "role": "user",
                        "parts": [{"text": f"{SUMMARY_PROMPT.strip()}\n\n{chunk_text}"}],
                    }],
                    "generationConfig": gen_config,
                },
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            entries_written += 1

    print(f"  OK Proceeding JSONL: {entries_written} chunk(s) → {jsonl_local_path}")
    return entries_written

def build_translation_chunks_jsonl(
    lang_chunks: dict[str, list[str]], 
    jsonl_local_path: str,
) -> int:
    """
    One JSONL entry per chunk per language.
    Each language gets its own chunk list (different sizes).
    """
    from .prompts import TRANSLATION_PROMPT

    gen_config = {"temperature": 0.1, "maxOutputTokens": 65535}
    entries_written = 0

    with open(jsonl_local_path, "w", encoding="utf-8") as f:
        for lang, chunks in lang_chunks.items():
            for chunk_idx, chunk_text in enumerate(chunks):
                prompt = TRANSLATION_PROMPT[lang].strip()
                full_text = f"{prompt}\n\n{chunk_text}"
                entry = {
                    "id": f"translation_chunk_{chunk_idx}_{lang}",
                    "request": {
                        "contents": [{
                            "role": "user",
                            "parts": [{"text": full_text}],
                        }],
                        "generationConfig": gen_config,
                    },
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                entries_written += 1

    total = sum(len(c) for c in lang_chunks.values())
    print(f"  OK Translation JSONL: {entries_written} entries "
          f"({len(lang_chunks)} languages · {total} total chunks) "
          f"→ {jsonl_local_path}")
    return entries_written
