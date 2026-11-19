from __future__ import annotations

import re

from .config import CHUNK_AUDIO_ON_UPLOAD, ENHANCE_AUDIO_ON_UPLOAD, INPUT_BUCKET, TRANSCRIPTION_QUALITY
from .gcs_utils import SUPPORTED_AUDIO_EXTENSIONS, upload_audio_file


CHUNK_RE = re.compile(
    r"^(?P<folder>.+)/chunk_(?P<num>\d{3,})_of_(?P<total>\d{3,})(?:_\d+-\d+)?\.flac$",
    re.IGNORECASE,
)


def expand_selected_chunk_groups(selected_uris: list[str], all_uris: list[str]) -> list[str]:
    selected = list(selected_uris)
    selected_set = set(selected)
    folders: set[str] = set()

    for uri in selected_uris:
        path = uri.replace("gs://", "", 1)
        path_without_bucket = path.split("/", 1)[1] if "/" in path else path
        match = CHUNK_RE.match(path_without_bucket)
        if match:
            folders.add(match.group("folder"))

    if not folders:
        return selected

    for uri in all_uris:
        path = uri.replace("gs://", "", 1)
        path_without_bucket = path.split("/", 1)[1] if "/" in path else path
        match = CHUNK_RE.match(path_without_bucket)
        if match and match.group("folder") in folders and uri not in selected_set:
            selected.append(uri)
            selected_set.add(uri)

    return selected


def ask_gcs_audio_location() -> str | None:
    print("\n" + "-" * 60)
    print("  GCS AUDIO LOCATION")
    print("-" * 60)
    print("  [1] All audio files in the bucket")
    print("  [2] Manual uploads only (uploads/manual/)")
    print()

    raw = input("  > Choose GCS location (1/2) [default: 1]: ").strip().lower()
    if raw in ("2", "manual", "m", "uploads/manual"):
        print("  Manual upload path selected: uploads/manual/")
        return "uploads/manual/"

    print("  Full bucket selected.")
    return None

def select_files_interactively(all_uris: list) -> list:
    print("\n" + "┌" + "─" * 58 + "┐")
    print("│  📂 Files found in your GCS bucket:" + " " * 21 + "│")
    print("├" + "─" * 58 + "┤")

    for i, uri in enumerate(all_uris, start=1):
        filename = uri.replace(f"gs://{INPUT_BUCKET}/", "")
        display = filename if len(filename) <= 50 else "..." + filename[-47:]
        print(f"│  [{i:>2}]  {display:<50}  │")

    print("└" + "─" * 58 + "┘")
    print()
    print("  💡 How to select:")
    print("     • Single file   → type:  1")
    print("     • Multiple      → type:  1,3,5")
    print("     • Range         → type:  1-5")
    print("     • All files     → type:  all")
    print()

    while True:
        raw = input("  ▶ Enter your selection: ").strip().lower()

        if not raw:
            print("  ⚠️  Nothing entered. Please try again.\n")
            continue

        if raw == "all":
            return all_uris

        selected_indices = set()
        parts = raw.replace(" ", "").split(",")
        valid = True

        for part in parts:
            if "-" in part:
                bounds = part.split("-")
                if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit():
                    selected_indices.update(range(int(bounds[0]), int(bounds[1]) + 1))
                else:
                    print(f"  ❌ Invalid range: '{part}'. Use format like 1-5\n")
                    valid = False
                    break
            elif part.isdigit():
                selected_indices.add(int(part))
            else:
                print(f"  ❌ Invalid input: '{part}'. Use numbers only.\n")
                valid = False
                break

        if not valid:
            continue

        out_of_range = [n for n in selected_indices if n < 1 or n > len(all_uris)]
        if out_of_range:
            print(f"  ❌ Numbers out of range: {out_of_range}. Valid: 1–{len(all_uris)}\n")
            continue

        selected_uris = [all_uris[i - 1] for i in sorted(selected_indices)]
        expanded_uris = expand_selected_chunk_groups(selected_uris, all_uris)
        if len(expanded_uris) != len(selected_uris):
            added = len(expanded_uris) - len(selected_uris)
            print(f"\n  Added {added} related chunk(s) from the same uploaded audio folder.")
            selected_uris = expanded_uris

        print(f"\n  ✅ You selected {len(selected_uris)} file(s):")
        for uri in selected_uris:
            print(f"     🎵 {uri.replace(f'gs://{INPUT_BUCKET}/', '')}")
        print()

        confirm = input("  ▶ Confirm? (yes / no): ").strip().lower()
        if confirm in ("yes", "y"):
            return selected_uris
        if confirm in ("no", "n"):
            print("\n  🔄 Restarting selection...\n")
            return select_files_interactively(all_uris)
        print("  ⚠️  Please type 'yes' or 'no'\n")


def ask_translation_options() -> list:
    print("\n" + "─" * 60)
    print("  🌐 TRANSLATION OPTIONS")
    print("─" * 60)
    print("  Do you want translations of the transcript?\n")
    print("  Available languages:")
    print("     [1]  Sinhala (සිංහල)")
    print("     [2]  Tamil (தமிழ்)")
    print("     [3]  English")
    print("     [0]  No translation needed")
    print()
    print("  You can select multiple: e.g. type  1,2  for Sinhala + Tamil")
    print("                                  type  1,2,3  for all three")
    print()

    raw = input("  ▶ Enter your choice: ").strip()

    if raw == "0" or raw == "":
        print("  ℹ️  No translation will be generated.")
        return []

    lang_map = {"1": "si", "2": "ta", "3": "en"}
    selected = []

    for part in raw.replace(" ", "").split(","):
        if part in lang_map:
            lang = lang_map[part]
            if lang not in selected:
                selected.append(lang)
        else:
            print(f"  ⚠️  Ignoring invalid option: '{part}'")

    lang_names = {"si": "Sinhala", "ta": "Tamil", "en": "English"}
    if selected:
        names = [lang_names[l] for l in selected]
        print(f"  ✅ Translation selected: {', '.join(names)}")
    else:
        print("  ℹ️  No valid options selected — no translation will be generated.")

    return selected


def ask_summary_option() -> bool:
    print("\n" + "─" * 60)
    print("  📋 SUMMARY OPTION")
    print("─" * 60)
    print("  Do you want a summary of each audio file?\n")
    print("  The summary will include: case overview, speakers identified,")
    print("  key points discussed, and outcome. Written in English.\n")

    raw = input("  ▶ Generate summary? (yes / no): ").strip().lower()

    if raw in ("yes", "y"):
        print("  ✅ Summary will be generated.")
        return True

    print("  ℹ️  No summary will be generated.")
    return False


def ask_transcription_quality(default: str | None = None) -> str:
    default = (default or TRANSCRIPTION_QUALITY or "fast").strip().lower()
    if default not in ("fast", "accurate"):
        default = "fast"

    print("\n" + "-" * 60)
    print("  TRANSCRIPTION QUALITY")
    print("-" * 60)
    print("  [1] Fast     (gemini flash; good quality; quickest)")
    print("  [2] Accurate (gemini pro; best accuracy; slower)")
    print()

    prompt = "  > Choose quality (1/2)"
    prompt += " [default: 1]: " if default == "fast" else " [default: 2]: "

    raw = input(prompt).strip().lower()
    if not raw:
        return default

    if raw in ("1", "fast", "f"):
        return "fast"
    if raw in ("2", "accurate", "a"):
        return "accurate"

    print("  Invalid choice; using default.")
    return default


def ask_upload_conversion() -> bool:
    print()
    print("  Upload mode:")
    print("     [1] Upload original file directly (single audio file, no conversion)")
    print("     [2] Convert to FLAC, then upload")
    print()

    raw = input("  > Choose upload mode (1/2) [default: 1]: ").strip().lower()
    if raw in ("2", "flac", "convert", "c"):
        print("  FLAC conversion enabled for this upload.")
        return True

    print("  Direct upload selected. The original audio file will be sent as one GCS object.")
    return False


def ask_audio_processing_options() -> tuple[bool, bool, bool]:
    print()
    print("  Audio preparation:")
    print("     [1] Enhance speech clarity and split into 10-minute chunks")
    print("     [2] Enhance speech clarity only")
    print("     [3] Convert to FLAC only")
    print("     [4] Upload original file directly")
    print()

    default_choice = "1" if ENHANCE_AUDIO_ON_UPLOAD and CHUNK_AUDIO_ON_UPLOAD else "4"
    raw = input(f"  > Choose audio preparation (1/2/3/4) [default: {default_choice}]: ").strip().lower()
    raw = raw or default_choice

    if raw in ("1", "enhance+chunk", "chunk", "chunks"):
        print("  Enhanced 10-minute chunk processing enabled.")
        return True, True, True
    if raw in ("2", "enhance", "e"):
        print("  Audio enhancement enabled.")
        return True, True, False
    if raw in ("3", "flac", "convert", "c"):
        print("  FLAC conversion enabled.")
        return True, False, False

    print("  Direct upload selected. The original audio file will be used.")
    return False, False, False


def upload_local_audio_files(bucket_name: str) -> list[str]:
    print("\n" + "-" * 60)
    print("  UPLOAD NEW AUDIO")
    print("-" * 60)
    print("  Paste a local file path to upload to your input bucket.")
    print(f"  Supported types: {', '.join(SUPPORTED_AUDIO_EXTENSIONS)}")
    print("  Tip: you can paste multiple paths separated by commas.")
    print()

    raw = input("  > Local file path(s) (blank to cancel): ").strip()
    if not raw:
        print("  No upload requested.")
        return []

    convert_to_flac, enhance_audio, chunk_audio = ask_audio_processing_options()
    paths = [p.strip() for p in raw.split(',') if p.strip()]
    uploaded: list[str] = []

    for pth in paths:
        try:
            print(f"\n  Uploading: {pth}")
            uris = upload_audio_file(
                pth,
                bucket_name,
                convert_to_flac=convert_to_flac,
                enhance_audio=enhance_audio,
                chunk_audio=chunk_audio,
            )
            uploaded.extend(uris)
        except Exception as e:
            print(f"  Failed to upload {pth}: {e}")

    if uploaded:
        print(f"\n  Uploaded {len(uploaded)} file(s):")
        for uri in uploaded:
            print(f"   - {uri}")

    return uploaded
