from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path

from .config import GEMINI_MODEL, S3_BUCKET, AWS_REGION, S3_NAMESPACE
from .config import CREATE_MPEG
from .s3_utils import upload_text, upload_bytes


import re


def remove_repetition_loops(transcript_text: str, repeat_marker: str = "[repeated lines]") -> tuple[str, int]:
    """Remove obvious repeated model-output loops from a transcript.

    Keeps the first copy of repeated text and replaces the looped copies with
    a marker so the transcript shows where model-loop cleanup happened.
    """

    if not transcript_text:
        return transcript_text, 0

    lines = transcript_text.splitlines()
    out: list[str] = []
    removed = 0
    i = 0
    marker = repeat_marker.strip()

    def append_marker(target: list[str]) -> None:
        if marker and (not target or target[-1] != marker):
            target.append(marker)

    def normalize_line(line: str) -> str:
        return re.sub(r"\s+", " ", line.strip()).lower()

    normalized = [normalize_line(line) for line in lines]
    max_block_lines = 8

    while i < len(lines):
        matched = False
        for block_size in range(1, max_block_lines + 1):
            block_end = i + block_size
            if block_end > len(lines):
                continue

            block = normalized[i:block_end]
            if not any(block):
                continue

            repeats = 1
            while (
                i + ((repeats + 1) * block_size) <= len(lines)
                and normalized[
                    i + (repeats * block_size): i + ((repeats + 1) * block_size)
                ] == block
            ):
                repeats += 1

            min_block_repeats = 5 if block_size == 1 else 3
            if repeats >= min_block_repeats:
                out.extend(lines[i:block_end])
                append_marker(out)
                removed += (repeats - 1) * block_size
                i += repeats * block_size
                matched = True
                break

        if not matched:
            out.append(lines[i])
            i += 1

    collapsed: list[str] = []
    i = 0
    keep_repeated_utterances = 1
    min_utterance_repeats = 5
    min_long_phrase_repeats = 4

    def normalize_utterance(line: str) -> str:
        text = re.sub(r"^\[[^\]]+\]:\s*", "", line.strip())
        text = re.sub(r"^Speaker\s*\d+:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"[.?.,;:!?\u0964\u0df4]+$", "", text)
        text = re.sub(r"\s+", " ", text).strip().lower()
        return text

    def split_label(line: str) -> tuple[str, str]:
        label_match = re.match(r"^(?P<label>\[[^\]]+\]:\s*)(?P<text>.*)$", line)
        if label_match:
            return label_match.group("label"), label_match.group("text")
        return "", line

    def collapse_repeated_phrases(line: str) -> tuple[str, int]:
        """Collapse comma-separated phrase loops inside a single transcript line."""

        label, text = split_label(line)

        parts = re.split(r"(\s*,\s*)", text)
        phrases = parts[::2]
        separators = parts[1::2]
        if len(phrases) < min_utterance_repeats:
            return line, 0

        def normalize_phrase(phrase: str) -> str:
            phrase = re.sub(r"[.?.,;:!?\u0964\u0df4]+$", "", phrase.strip())
            phrase = re.sub(r"\s+", " ", phrase).strip().lower()
            return phrase

        rebuilt: list[str] = []
        removed_phrases = 0
        phrase_index = 0

        while phrase_index < len(phrases):
            phrase = phrases[phrase_index]
            normalized_phrase = normalize_phrase(phrase)
            if not normalized_phrase:
                rebuilt.append(phrase)
                phrase_index += 1
                continue

            next_index = phrase_index + 1
            while next_index < len(phrases) and normalize_phrase(phrases[next_index]) == normalized_phrase:
                next_index += 1

            repeat_count = next_index - phrase_index
            min_repeats_for_phrase = (
                min_long_phrase_repeats
                if len(normalized_phrase) >= 12
                else min_utterance_repeats
            )
            if repeat_count >= min_repeats_for_phrase:
                rebuilt.append(phrase)
                if marker:
                    rebuilt.append(f", {marker}")
                removed_phrases += repeat_count - 1
            else:
                for kept_index in range(phrase_index, next_index):
                    if kept_index > phrase_index and kept_index - 1 < len(separators):
                        rebuilt.append(separators[kept_index - 1])
                    rebuilt.append(phrases[kept_index])

            phrase_index = next_index

            if phrase_index < len(phrases) and phrase_index - 1 < len(separators):
                rebuilt.append(separators[phrase_index - 1])

        return label + "".join(rebuilt), removed_phrases

    def collapse_repeated_word_sequences(line: str) -> tuple[str, int]:
        """Collapse repeated space-separated word or phrase loops inside one line."""

        label, text = split_label(line)
        tokens = re.findall(r"\S+", text)
        if len(tokens) < min_utterance_repeats:
            return line, 0

        def normalize_token(token: str) -> str:
            token = re.sub(r"^[^\w\u0d80-\u0dff\u0b80-\u0bff]+", "", token, flags=re.UNICODE)
            token = re.sub(r"[^\w\u0d80-\u0dff\u0b80-\u0bff]+$", "", token, flags=re.UNICODE)
            return token.lower()

        normalized_tokens = [normalize_token(token) for token in tokens]
        rebuilt: list[str] = []
        removed_tokens = 0
        token_index = 0
        max_phrase_words = min(16, max(1, len(tokens) // 3))

        while token_index < len(tokens):
            matched = False
            for phrase_size in range(1, max_phrase_words + 1):
                phrase_end = token_index + phrase_size
                if phrase_end > len(tokens):
                    break

                phrase = normalized_tokens[token_index:phrase_end]
                if not all(phrase):
                    continue

                repeats = 1
                while (
                    token_index + ((repeats + 1) * phrase_size) <= len(tokens)
                    and normalized_tokens[
                        token_index + (repeats * phrase_size): token_index + ((repeats + 1) * phrase_size)
                    ] == phrase
                ):
                    repeats += 1

                min_repeats_for_words = 6 if phrase_size == 1 else 4 if phrase_size <= 3 else 3
                if repeats >= min_repeats_for_words:
                    rebuilt.extend(tokens[token_index:phrase_end])
                    if marker:
                        rebuilt.append(marker)
                    removed_tokens += (repeats - 1) * phrase_size
                    token_index += repeats * phrase_size
                    matched = True
                    break

            if not matched:
                rebuilt.append(tokens[token_index])
                token_index += 1

        if not removed_tokens:
            return line, 0
        return label + " ".join(rebuilt), removed_tokens

    while i < len(out):
        utterance = normalize_utterance(out[i])
        if not utterance:
            collapsed.append(out[i])
            i += 1
            continue

        j = i + 1
        while j < len(out) and normalize_utterance(out[j]) == utterance:
            j += 1

        repeat_count = j - i
        if repeat_count >= min_utterance_repeats:
            collapsed.extend(out[i:i + keep_repeated_utterances])
            append_marker(collapsed)
            removed += repeat_count - keep_repeated_utterances
        else:
            collapsed.extend(out[i:j])
        i = j

    phrase_collapsed: list[str] = []
    for line in collapsed:
        fixed_line, removed_phrases = collapse_repeated_phrases(line)
        fixed_line, removed_words = collapse_repeated_word_sequences(fixed_line)
        phrase_collapsed.append(fixed_line)
        removed += removed_phrases
        removed += removed_words

    return "\n".join(phrase_collapsed), removed

def normalize_transcript_speakers(transcript_text: str) -> str:
    """Post-process transcript to improve speaker label stability.

    1. Renumber [Speaker N] to contiguous numbering in order of first appearance.
       This fixes skips like Speaker 4 without Speaker 3.
    2. If a speaker says "my name is: X" (or "my name is X"), relabel that
       speaker to [X] for the remainder of the transcript.

    This is best-effort text normalization; it cannot fix mis-attribution caused
    by the model itself.
    """

    if not transcript_text:
        return transcript_text

    lines = transcript_text.splitlines()

    # Pass 1: parse labels and build contiguous speaker mapping.
    speaker_order: list[str] = []
    seen: set[str] = set()
    label_re = re.compile(r'^\[(?P<label>[^\]]+)\]:\s*(?P<text>.*)$')

    for ln in lines:
        m = label_re.match(ln)
        if not m:
            continue
        label = m.group('label').strip()
        if label not in seen:
            seen.add(label)
            speaker_order.append(label)

    contiguous_map: dict[str, str] = {}
    speaker_n_re = re.compile(r'^Speaker\s*(\d+)$', re.IGNORECASE)
    next_num = 1

    for label in speaker_order:
        if speaker_n_re.match(label):
            contiguous_map[label] = f"Speaker {next_num}"
            next_num += 1
        else:
            # Keep non-standard labels as-is.
            contiguous_map[label] = label

    # Pass 2: walk lines, capture names, relabel forward.
    # We only capture English "my name is" as requested.
    name_re = re.compile(r'\bmy\s+name\s+is\s*:??\s*(?P<name>[A-Za-z][A-Za-z .\'\-]{0,80})', re.IGNORECASE)

    named_label_for: dict[str, str] = {}  # contiguous label -> Name
    used_names: set[str] = set()

    out_lines: list[str] = []
    for ln in lines:
        m = label_re.match(ln)
        if not m:
            out_lines.append(ln)
            continue

        raw_label = m.group('label').strip()
        text = m.group('text')

        base_label = contiguous_map.get(raw_label, raw_label)

        # Capture name if present.
        nm = name_re.search(text)
        if nm:
            name = nm.group('name').strip()
            # Truncate at common sentence boundaries to avoid capturing the rest of a sentence.
            for cut in ('.', ',', ';', ':', '!', '?'):
                if cut in name:
                    name = name.split(cut, 1)[0].strip()
            # Avoid collisions.
            if name and name not in used_names:
                named_label_for[base_label] = name
                used_names.add(name)

        final_label = named_label_for.get(base_label, base_label)
        out_lines.append(f"[{final_label}]: {text}" if text is not None else f"[{final_label}]:")

    return "\n".join(out_lines)


def _srt_time(value: str) -> str:
    parts = value.strip().replace(",", ".").split(".", 1)
    main = parts[0]
    millis = (parts[1] if len(parts) > 1 else "000")[:3].ljust(3, "0")
    h, m, s = [int(part) for part in main.split(":")]
    return f"{h:02d}:{m:02d}:{s:02d},{millis}"


def transcript_text_to_srt(transcript_text: str) -> str:
    """Build SRT captions from transcript lines with timestamp ranges.

    Expected line shape:
      [00:00:01.000 - 00:00:04.500] [Speaker 1]: text
    Continuation lines without timestamps are appended to the previous cue.
    """

    if not transcript_text:
        return ""

    range_re = re.compile(
        r"^\s*\[?(?P<start>\d{1,2}:\d{2}:\d{2}(?:[\.,]\d{1,3})?)\s*(?:-->|-|–|—|to)\s*"
        r"(?P<end>\d{1,2}:\d{2}:\d{2}(?:[\.,]\d{1,3})?)\]?\s*(?P<text>.*)$",
        re.IGNORECASE,
    )
    cues: list[tuple[str, str, list[str]]] = []

    for raw_line in transcript_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = range_re.match(line)
        if match:
            cue_text = match.group("text").strip()
            cues.append((_srt_time(match.group("start")), _srt_time(match.group("end")), [cue_text] if cue_text else []))
        elif cues and not line.startswith(("COURT AUDIO TRANSCRIPT", "=", "Source File", "END")):
            cues[-1][2].append(line)

    blocks: list[str] = []
    for idx, (start, end, lines) in enumerate(cues, start=1):
        text = "\n".join(line for line in lines if line).strip()
        if not text:
            continue
        blocks.append(f"{idx}\n{start} --> {end}\n{text}")

    return "\n\n".join(blocks) + ("\n" if blocks else "")

def _slugify(name: str) -> str:
    if not name:
        return "unknown"
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return s or "unknown"


def _sanitize_case_folder(case_number: str) -> str:
    raw = (case_number or "").strip().upper()
    raw = re.sub(r"[^A-Z0-9_-]+", "-", raw)
    raw = re.sub(r"-{2,}", "-", raw).strip("-_")
    return raw or "CASE"


def _lookup_result_text(results: dict, idx: int, filename_stem: str, result_type: str) -> str:
    expected_key = f"{idx}|{filename_stem}|{result_type}"
    text = str(results.get(expected_key, "") or "").strip()
    if text:
        return text

    suffix = f"|{filename_stem}|{result_type}"
    matches = [
        str(value or "").strip()
        for key, value in results.items()
        if str(key).endswith(suffix) and str(value or "").strip()
    ]
    if len(matches) == 1:
        return matches[0]

    type_matches = [
        str(value or "").strip()
        for key, value in results.items()
        if str(key).endswith(f"|{result_type}") and str(value or "").strip()
    ]
    if len(type_matches) == 1:
        return type_matches[0]

    return ""


def save_results_locally(
    audio_uris: list,
    results: dict,
    translate_langs: list,
    include_summary: bool,
    output_dir: str = "transcripts",
    result_date: str | None = None,
    court: str = "",
    original_filename: str = "",
    case_folder: str = "",
):
    lang_names = {"si": "Sinhala", "ta": "Tamil", "en": "English"}
    s3_entries: list[dict] = []
    saved = []
    today = result_date or datetime.now().strftime("%Y-%m-%d")
    court_slug = _slugify(court)
    resolved_case_folder = _sanitize_case_folder(case_folder)

    for idx, uri in enumerate(audio_uris):
        filename_stem = uri.split("/")[-1].rsplit(".", 1)[0]
        folder_name = resolved_case_folder or filename_stem
        folder_key = f"{S3_NAMESPACE}/{court_slug}/{today}/{folder_name}"

        print(f"\n  📄 Preparing S3 files for: {filename_stem}")
        print(f"     📁 S3 Prefix: {folder_key}")

        transcript_text = _lookup_result_text(results, idx, filename_stem, "transcript")
        transcript_text = normalize_transcript_speakers(transcript_text)
        transcript_text, removed_repeated_lines = remove_repetition_loops(transcript_text, repeat_marker="")

        if transcript_text:
           # header = build_header(uri, "COURT AUDIO TRANSCRIPT", "Original transcript — all languages preserved in their own script")
           # body = header + "\n" + transcript_text
            try:
                key = f"{folder_key}/transcript.txt"
                upload_text(S3_BUCKET, key, transcript_text, content_type="text/plain; charset=utf-8", region=AWS_REGION)
                s3_entries.append({"name": "transcript.txt", "s3Key": key, "size": len(transcript_text.encode("utf-8")), "updatedAt": datetime.now().isoformat(timespec="seconds")})
            except Exception as exc:
                raise RuntimeError(f"Could not upload transcript.txt to S3: {exc}") from exc
            srt_text = transcript_text_to_srt(transcript_text)
            if srt_text:
                try:
                    srt_key = f"{folder_key}/transcript.srt"
                    upload_text(S3_BUCKET, srt_key, srt_text, content_type="application/x-subrip; charset=utf-8", region=AWS_REGION)
                    s3_entries.append({"name": "transcript.srt", "s3Key": srt_key, "size": len(srt_text.encode("utf-8")), "updatedAt": datetime.now().isoformat(timespec="seconds")})
                except Exception:
                    pass
            if CREATE_MPEG:
                try:
                    from gtts import gTTS
                    bio = io.BytesIO()
                    tts = gTTS(text=transcript_text, lang="en")
                    tts.write_to_fp(bio)
                    bio.seek(0)
                    mp3_bytes = bio.read()
                    mp3_key = f"{folder_key}/transcript.mp3"
                    upload_bytes(S3_BUCKET, mp3_key, mp3_bytes, content_type="audio/mpeg", region=AWS_REGION)
                    s3_entries.append({"name": "transcript.mp3", "s3Key": mp3_key, "size": len(mp3_bytes), "updatedAt": datetime.now().isoformat(timespec="seconds")})
                except Exception:
                    pass
            if removed_repeated_lines:
                print(f"     WARNING Removed {removed_repeated_lines} repeated model-output line(s)")
        else:
            print(f"     ⚠️  No transcript result found for {filename_stem}")

        for lang in translate_langs:
            trans_text = _lookup_result_text(results, idx, filename_stem, f"translate_{lang}")
            lang_name = lang_names[lang]

            if trans_text:
                name = f"translation_{lang_name.lower()}.txt"
               # header = build_header(uri, f"TRANSLATION — {lang_name.upper()}", f"Full translation of the audio into {lang_name}")
               # body = header + "\n" + trans_text
                try:
                    trans_key = f"{folder_key}/{name}"
                    upload_text(S3_BUCKET, trans_key, trans_text, content_type="text/plain; charset=utf-8", region=AWS_REGION)
                    s3_entries.append({"name": name, "s3Key": trans_key, "size": len(trans_text.encode("utf-8")), "updatedAt": datetime.now().isoformat(timespec="seconds")})
                except Exception:
                    pass
            else:
                print(f"     ⚠️  No {lang_name} translation result found for {filename_stem}")

        if include_summary:
            summary_text = _lookup_result_text(results, idx, filename_stem, "summary")

            if summary_text:
                name = "summary.txt"
               # header = build_header(uri, "CASE SUMMARY", "summary of the court audio")
               # body = header + "\n" + summary_text
                try:
                    key = f"{folder_key}/{name}"
                    upload_text(S3_BUCKET, key, summary_text, content_type="text/plain; charset=utf-8", region=AWS_REGION)
                    s3_entries.append({"name": name, "s3Key": key, "size": len(summary_text.encode("utf-8")), "updatedAt": datetime.now().isoformat(timespec="seconds")})
                except Exception:
                    pass
            else:
                print(f"     ⚠️  No summary result found for {filename_stem}")

    # Return tuple: folder_name and s3 entries
    first_folder = None
    if audio_uris:
        first_folder = resolved_case_folder or audio_uris[0].split("/")[-1].rsplit(".", 1)[0]
    return first_folder or "", s3_entries


def build_header(uri: str, title: str, description: str) -> str:
    lines = []
    lines.append(title)
    lines.append("=" * 60)
    lines.append(f"Source File  : {uri}")

    lines.append("=" * 60)
    lines.append("")
    return "\n".join(lines)


def write_txt(path: str, header: str, content: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(content)
        f.write("\n\n" + "=" * 60 + "\nEND")


def transcript_text_to_mpeg_file(transcript_text: str, out_path: str, lang: str = "en") -> str | None:
    """Convert `transcript_text` to an MP3 (MPEG audio) file at `out_path`.

    Uses gTTS (Google Text-to-Speech). Returns the path on success, or None.
    """
    if not transcript_text or not transcript_text.strip():
        return None

    try:
        # Import here so the rest of the codebase doesn't require gTTS unless used.
        from gtts import gTTS

        tts = gTTS(text=transcript_text, lang=lang)
        # Ensure parent dir exists
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        tts.save(out_path)
        return out_path
    except Exception as e:
        print(f"     ⚠️  Could not create MPEG audio: {e}")
        return None
