from __future__ import annotations
import difflib 
import re
from dataclasses import dataclass

from .config import AUDIO_CHUNK_SECONDS

SKIPPED_AUDIO_SCHEME = "skip://"
SKIPPED_CHUNK_TRANSCRIPT = "[Hearing issue identified: long silence or no clear speech]"

CHUNK_RE = re.compile(
    r"^(?P<folder>.+)/chunk_(?P<num>\d{3,})_of_(?P<total>\d{3,})(?:_(?P<start>\d+)-(?P<end>\d+))?\.flac$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AudioItem:
    uri: str
    index: int
    filename_stem: str
    chunk_number: int | None = None
    total_chunks: int | None = None
    parent_uri: str | None = None
    start_seconds: int | None = None
    end_seconds: int | None = None

    @property
    def transcript_id(self) -> str:
        return f"{self.index}|{self.filename_stem}|transcript"

    @property
    def is_chunk(self) -> bool:
        return self.chunk_number is not None and self.parent_uri is not None

    @property
    def section_start_seconds(self) -> int | None:
        if self.chunk_number is None:
            return None
        return normalize_chunk_time_range(
            self.chunk_number,
            self.total_chunks,
            self.start_seconds,
            self.end_seconds,
        )[0]

    @property
    def section_end_seconds(self) -> int | None:
        if self.chunk_number is None:
            return None
        return normalize_chunk_time_range(
            self.chunk_number,
            self.total_chunks,
            self.start_seconds,
            self.end_seconds,
        )[1]


def filename_stem_from_uri(uri: str) -> str:
    return uri.split("/")[-1].rsplit(".", 1)[0]


def is_skipped_audio_uri(uri: str) -> bool:
    return (uri or "").startswith(SKIPPED_AUDIO_SCHEME)


def format_hms(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def normalize_chunk_time_range(
    chunk_number: int,
    total_chunks: int | None,
    start_seconds: int | None,
    end_seconds: int | None,
) -> tuple[int, int]:
    """Return safe display timestamps for a fixed-duration chunk section."""
    if start_seconds is not None and end_seconds is not None:
        start = max(0, start_seconds)
        end = end_seconds
        if end <= start:
            return start, start + max(1, AUDIO_CHUNK_SECONDS)

        inferred_chunk_seconds = AUDIO_CHUNK_SECONDS
        if chunk_number > 1 and start > 0:
            inferred_chunk_seconds = max(60, int(round(start / (chunk_number - 1))))
        elif end > start:
            inferred_chunk_seconds = max(60, end - start)

        if end - start <= inferred_chunk_seconds + 1:
            return start, end

        if total_chunks is not None and chunk_number == total_chunks:
            inferred_total_duration = end - start
            if inferred_total_duration <= start:
                inferred_total_duration = start + 1
            return start, inferred_total_duration

        return start, start + inferred_chunk_seconds

    expected_start = max(0, (chunk_number - 1) * AUDIO_CHUNK_SECONDS)
    expected_end = chunk_number * AUDIO_CHUNK_SECONDS
    return expected_start, expected_end


def inspect_audio_uri(uri: str, index: int) -> AudioItem:
    if is_skipped_audio_uri(uri):
        path = uri.replace(SKIPPED_AUDIO_SCHEME, "", 1)
    else:
        path = uri.replace("gs://", "", 1)
    path_without_bucket = path.split("/", 1)[1] if "/" in path else path
    match = CHUNK_RE.match(path_without_bucket)
    filename_stem = filename_stem_from_uri(uri)

    if not match:
        return AudioItem(uri=uri, index=index, filename_stem=filename_stem)

    folder = match.group("folder")
    chunk_number = int(match.group("num"))
    total_chunks = int(match.group("total"))
    start_seconds = int(match.group("start")) if match.group("start") else None
    end_seconds = int(match.group("end")) if match.group("end") else None
    bucket = path.split("/", 1)[0]
    parent_uri = f"gs://{bucket}/{folder}.flac"

    return AudioItem(
        uri=uri,
        index=index,
        filename_stem=filename_stem,
        chunk_number=chunk_number,
        total_chunks=total_chunks,
        parent_uri=parent_uri,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
    )


def merge_chunk_transcripts(audio_uris: list[str], transcripts: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """Merge per-chunk transcription results into per-source transcript results."""
    parent_order: list[str] = []
    parent_chunks: dict[str, list[tuple[int, str, int | None, int | None]]] = {}
    merged_results: dict[str, str] = {}
    single_items: list[tuple[str, str]] = []

    for idx, uri in enumerate(audio_uris):
        item = inspect_audio_uri(uri, idx)
        text = _lookup_transcript_text(transcripts, item.transcript_id, item.filename_stem)

        if item.is_chunk:
            parent_uri = item.parent_uri or uri
            if parent_uri not in parent_order:
                parent_order.append(parent_uri)
            parent_chunks.setdefault(parent_uri, []).append(
                (item.chunk_number or 0, text, item.section_start_seconds, item.section_end_seconds)
            )
            continue

        if uri not in parent_order:
            parent_order.append(uri)
        single_items.append((uri, text))

    parent_index = {uri: idx for idx, uri in enumerate(parent_order)}

    for uri, text in single_items:
        idx = parent_index[uri]
        stem = filename_stem_from_uri(uri)
        merged_results[f"{idx}|{stem}|transcript"] = text

    for parent_uri, chunks in parent_chunks.items():
            idx  = parent_index[parent_uri]
            stem = filename_stem_from_uri(parent_uri)

            sorted_chunks    = sorted(chunks, key=lambda c: c[0])
            accumulated_text = ""

            for chunk_number, raw_text, start_seconds, end_seconds in sorted_chunks:
                text = (raw_text or "").strip()
                if not text:
                    continue

                if not accumulated_text:
                    # First chunk — take everything as-is, no overlap to remove
                    accumulated_text = text
                    continue

                head_lines = [l for l in accumulated_text.splitlines() if l.strip()]
                tail_lines = [l for l in text.splitlines() if l.strip()]

                # Step 1 — find where the overlap region ends in this chunk
                overlap_end = _find_overlap_boundary_line(head_lines, tail_lines)

                # Step 2 — use the shared overlap lines to build a label map
                #           and correct any wrong labels throughout the tail
                reconciled_text  = _reconcile_speaker_labels(
                    accumulated_text, text, overlap_end
                )
                reconciled_lines = [
                    l for l in reconciled_text.splitlines() if l.strip()
                ]

                # Step 3 — append only genuinely new lines (skip the overlap region)
                new_lines = reconciled_lines[overlap_end:]

                if new_lines:
                    accumulated_text = (
                        accumulated_text.rstrip()
                        + "\n\n"
                        + "\n".join(new_lines)
                    )
                    print(
                        f"[merge] Chunk {chunk_number}: "
                        f"removed {overlap_end} overlap line(s), "
                        f"added {len(new_lines)} new line(s)"
                    )
                else:
                    # Entire chunk was overlap — this can happen on the last chunk
                    # if it is very short or audio ended during the overlap region
                    print(
                        f"[merge] Chunk {chunk_number}: "
                        f"no new content after overlap removal "
                        f"({overlap_end} overlap lines)"
                    )
            merged_results[f"{idx}|{stem}|transcript"] = accumulated_text

    return parent_order, merged_results


def _lookup_transcript_text(transcripts: dict[str, str], expected_key: str, filename_stem: str) -> str:
    """Return transcript text, allowing index drift but keeping stem matching strict."""
    text = transcripts.get(expected_key, "")
    if text:
        return text

    suffix = f"|{filename_stem}|transcript"
    matches = [
        str(value or "").strip()
        for key, value in transcripts.items()
        if key.endswith(suffix) and str(value or "").strip()
    ]
    if len(matches) == 1:
        return matches[0]

    return ""


def count_chunk_groups(audio_uris: list[str]) -> tuple[int, int]:
    groups: set[str] = set()
    chunks = 0
    for idx, uri in enumerate(audio_uris):
        item = inspect_audio_uri(uri, idx)
        if item.is_chunk and item.parent_uri:
            groups.add(item.parent_uri)
            chunks += 1
    return len(groups), chunks

def _find_overlap_boundary_line(
    head_lines: list[str],
    tail_lines: list[str],
    min_ratio: float = 0.75,
) -> int:
    """
    Find the line index in tail_lines where genuinely new content starts.
    Everything before this index is duplicated overlap content that already
    exists in head_lines and must be discarded.
    Returns 0 if no clear overlap is found.
    """
    head_window = [l for l in head_lines if l.strip()][-20:]
    tail_window  = [l for l in tail_lines if l.strip()][:20]

    if not head_window or not tail_window:
        return 0

    last_match_in_tail = -1

    for head_line in head_window:
        # Strip speaker label — compare spoken content only.
        # This handles the case where the label is wrong in the tail
        # but the words are identical.
        head_content = re.sub(r"^\[[^\]]+\]:\s*", "", head_line).strip()
        if len(head_content) < 8:
            continue  # too short to match reliably
        for tail_idx, tail_line in enumerate(tail_window):
            tail_content = re.sub(r"^\[[^\]]+\]:\s*", "", tail_line).strip()
            ratio = difflib.SequenceMatcher(
                None, head_content.lower(), tail_content.lower()
            ).ratio()
            if ratio >= min_ratio and tail_idx > last_match_in_tail:
                last_match_in_tail = tail_idx

    return last_match_in_tail + 1 if last_match_in_tail >= 0 else 0


def _reconcile_speaker_labels(
    head_text: str,
    tail_text: str,
    overlap_end: int,
) -> str:
    """
    Use the overlapping shared lines as proof to build an exact speaker
    label mapping, then apply it to the entire tail text.

    Example:
        head has: [Judge]: on what grounds
        tail has: [Speaker 2]: on what grounds   ← same words, wrong label
        → builds map {Speaker 2 → Judge}
        → applies to ALL lines in tail, not just overlap region

    Returns unchanged tail_text if overlap_end is 0 or labels already match.
    """
    if overlap_end == 0:
        return tail_text

    head_lines   = [l for l in head_text.splitlines() if l.strip()]
    tail_lines   = [l for l in tail_text.splitlines() if l.strip()]
    head_window  = head_lines[-20:]
    tail_overlap = tail_lines[:overlap_end]   # only the shared region used for mapping

    label_map: dict[str, str] = {}

    for tail_line in tail_overlap:
        tail_m = re.match(r"^\[([^\]]+)\]:\s*(.+)$", tail_line.strip())
        if not tail_m:
            continue
        tail_label   = tail_m.group(1).strip()
        tail_content = tail_m.group(2).strip()

        if tail_label in label_map:
            continue  # already resolved this label

        best_ratio = 0.0
        best_label = None

        for head_line in head_window:
            head_m = re.match(r"^\[([^\]]+)\]:\s*(.+)$", head_line.strip())
            if not head_m:
                continue
            head_content = head_m.group(2).strip()
            ratio = difflib.SequenceMatcher(
                None, tail_content.lower(), head_content.lower()
            ).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_label = head_m.group(1).strip()

        if best_ratio >= 0.75 and best_label and tail_label != best_label:
            label_map[tail_label] = best_label
            print(
                f"[reconcile] [{tail_label}] → [{best_label}] "
                f"(confidence={best_ratio:.2f}, "
                f"shared line: {tail_content[:50]!r})"
            )

    if not label_map:
        return tail_text  # labels were already consistent, nothing to remap

    result = []
    for line in tail_text.splitlines():
        m = re.match(r"^\[([^\]]+)\]:(.*)", line.strip())
        if m and m.group(1).strip() in label_map:
            old = m.group(1).strip()
            new = label_map[old]
            result.append(f"[{new}]:{m.group(2)}")
        else:
            result.append(line)

    return "\n".join(result)