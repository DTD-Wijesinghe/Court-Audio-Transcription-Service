from __future__ import annotations

import os
import tempfile
from datetime import datetime

from .batch_jobs import (
    build_text_jsonl,
    build_transcription_jsonl,
    submit_batch_job,
    wait_for_batch_job,
)
from .chunking import count_chunk_groups, merge_chunk_transcripts
from .config import (
    ACCURATE_MODEL,
    FAST_MODEL,
    GEMINI_MODEL,
    INPUT_BUCKET,
    LOCATION,
    OUTPUT_BUCKET,
    PROJECT_ID,
    S3_BUCKET,
    S3_NAMESPACE,
    TRANSCRIPTION_QUALITY,
)
from .gcs_utils import download_and_parse_results, list_audio_files_in_bucket, upload_to_gcs
from .interactive import (
    ask_gcs_audio_location,
    ask_summary_option,
    ask_translation_options,
    ask_transcription_quality,
    select_files_interactively,
    upload_local_audio_files,
)
from .output_utils import save_results_locally


def run_transcription_batch(
    audio_uris: list[str],
    timestamp: str,
    transcription_quality: str,
    transcription_model: str,
) -> dict[str, str]:
    chunk_group_count, chunk_count = count_chunk_groups(audio_uris)
    if chunk_count:
        print("\nBuilding parallel chunk transcription batch input...")
        print(f"  Submitting {chunk_count} chunk request(s) from {chunk_group_count} source file(s).")
        print("  Results will be merged by chunk number and time range after the batch returns.")
    else:
        print("\nBuilding transcription batch input...")

    jsonl_filename = f"batch_transcription_{timestamp}.jsonl"
    jsonl_local = os.path.join(tempfile.gettempdir(), jsonl_filename)
    jsonl_gcs_blob = f"batch_inputs/{jsonl_filename}"

    build_transcription_jsonl(audio_uris, jsonl_local, quality=transcription_quality)

    print("\nUploading transcription batch input to GCS...")
    jsonl_gcs_uri = upload_to_gcs(jsonl_local, INPUT_BUCKET, jsonl_gcs_blob)

    output_gcs_prefix = f"gs://{OUTPUT_BUCKET}/batch_outputs/{timestamp}_transcription/"

    print("\nSubmitting transcription job to Vertex AI...\n")
    job, client = submit_batch_job(jsonl_gcs_uri, output_gcs_prefix, model_override=transcription_model)
    success, _ = wait_for_batch_job(job, client)

    if not success:
        print("\nTranscription job failed.")
        return {}

    print("\nDownloading transcription results...\n")
    return download_and_parse_results(output_gcs_prefix)


def main():
    print("\n" + "=" * 60)
    print("   Vertex AI Gemini - Court Audio Batch Transcription")
    print("=" * 60)
    print(f"   Project  : {PROJECT_ID}")
    print(f"   Location : {LOCATION}")
    print(f"   Model    : {GEMINI_MODEL}")
    print(f"   Bucket   : gs://{INPUT_BUCKET}")
    print("=" * 60)

    upload_now = input("\nUpload new audio files to the input bucket now? (yes / no): ").strip().lower()
    uploaded_uris: list[str] = []
    if upload_now in ("yes", "y"):
        uploaded_uris = upload_local_audio_files(INPUT_BUCKET)

    transcription_quality = ask_transcription_quality(TRANSCRIPTION_QUALITY)
    transcription_model = ACCURATE_MODEL if transcription_quality == "accurate" else FAST_MODEL

    if uploaded_uris:
        audio_uris = uploaded_uris
        print("\nUsing the newly uploaded audio for this run.")
        print(f"   Selected {len(audio_uris)} uploaded file/chunk(s) automatically.")
        for uri in audio_uris:
            print(f"   - {uri}")
    else:
        gcs_audio_prefix = ask_gcs_audio_location()
        print("\nScanning GCS bucket for audio files...\n")
        all_uris = list_audio_files_in_bucket(INPUT_BUCKET, prefix=gcs_audio_prefix)

        if not all_uris:
            print("No audio files found. Upload your files to the bucket first.")
            return

        print(f"\nFound {len(all_uris)} audio file(s).\n")
        audio_uris = select_files_interactively(all_uris)

    translate_langs = ask_translation_options()
    include_summary = ask_summary_option()

    lang_names = {"si": "Sinhala", "ta": "Tamil", "en": "English"}
    chunk_group_count, chunk_count = count_chunk_groups(audio_uris)
    parent_audio_count = len(audio_uris) - chunk_count + chunk_group_count
    total_tasks = len(audio_uris) + parent_audio_count * (len(translate_langs) + (1 if include_summary else 0))

    print("\n" + "-" * 60)
    print("  TASK SUMMARY")
    print("-" * 60)
    print(f"  Audio files     : {parent_audio_count}")
    if chunk_count:
        print(f"  Audio chunks    : {chunk_count} chunk request(s) from {chunk_group_count} source file(s)")
    print(f"  Transcription   : Always included ({transcription_quality})")
    if translate_langs:
        print(f"  Translations    : {', '.join(lang_names[l] for l in translate_langs)}")
    else:
        print("  Translations    : None")
    print(f"  Summary         : {'Yes' if include_summary else 'No'}")
    print(f"  Total Gemini requests to Google: {total_tasks}")
    print("-" * 60)
    print()

    confirm = input("  Start processing? (yes / no): ").strip().lower()
    if confirm not in ("yes", "y"):
        print("  Cancelled.")
        return

    process_start_time = datetime.now()
    print("\n" + "=" * 60)
    print(f"  Process started : {process_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    transcripts = run_transcription_batch(audio_uris, timestamp, transcription_quality, transcription_model)

    if not transcripts:
        print("No transcription results found.")
        return

    parent_audio_uris, transcripts = merge_chunk_transcripts(audio_uris, transcripts)
    if len(parent_audio_uris) != len(audio_uris):
        print(f"\n  Merged chunk transcripts into {len(parent_audio_uris)} source transcript(s).")

    needs_job2 = translate_langs or include_summary
    if needs_job2:
        print("\nBuilding translation/summary batch input...")
        jsonl_filename2 = f"batch_text_{timestamp}.jsonl"
        jsonl_local2 = os.path.join(tempfile.gettempdir(), jsonl_filename2)
        jsonl_gcs_blob2 = f"batch_inputs/{jsonl_filename2}"

        build_text_jsonl(parent_audio_uris, transcripts, translate_langs, include_summary, jsonl_local2)

        print("\nUploading translation/summary batch input to GCS...")
        jsonl_gcs_uri2 = upload_to_gcs(jsonl_local2, INPUT_BUCKET, jsonl_gcs_blob2)

        output_gcs_prefix_2 = f"gs://{OUTPUT_BUCKET}/batch_outputs/{timestamp}_text/"

        print("\nSubmitting translation/summary job to Vertex AI...\n")
        job2, client2 = submit_batch_job(jsonl_gcs_uri2, output_gcs_prefix_2, model_override=FAST_MODEL)
        success2, _ = wait_for_batch_job(job2, client2)

        if not success2:
            print("\nTranslation/summary job failed.")
            return

        print("\nDownloading translation/summary results...\n")
        text_results = download_and_parse_results(output_gcs_prefix_2)
        all_results = {**transcripts, **text_results}
    else:
        all_results = transcripts

    print("\nSaving all results to S3...\n")
    folder_name, s3_files = save_results_locally(parent_audio_uris, all_results, translate_langs, include_summary)

    print("\n" + "=" * 60)
    print(f"  All done. {len(s3_files)} file(s) uploaded to S3.")

    process_end_time = datetime.now()
    elapsed_seconds = int((process_end_time - process_start_time).total_seconds())
    hours, remainder = divmod(elapsed_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    print(f"  Process ended   : {process_end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Elapsed time    : {hours:02d}:{minutes:02d}:{seconds:02d}")
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"  S3 path: s3://{S3_BUCKET}/{S3_NAMESPACE}/{today}/{folder_name}/")
    for entry in s3_files:
        print(f"  - {entry.get('s3Key')}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
