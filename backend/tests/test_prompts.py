import json
import tempfile
import unittest
from pathlib import Path

from court_transcription.batch_jobs import build_transcription_jsonl
from court_transcription.prompts import (
    TRANSCRIPTION_SYSTEM_INSTRUCTION,
    TRANSCRIPT_COVERAGE_AUDIT_PROMPT,
    TRANSCRIPT_REPETITION_REPAIR_PROMPT,
    build_transcription_prompt,
)


class NativeScriptPromptTests(unittest.TestCase):
    def test_system_instruction_prohibits_cross_script_transliteration(self):
        self.assertIn("Never transliterate Tamil speech into Sinhala letters", TRANSCRIPTION_SYSTEM_INSTRUCTION)
        self.assertIn("Do not choose one script for an entire speaker", TRANSCRIPTION_SYSTEM_INSTRUCTION)

    def test_transcription_prompt_requires_per_span_language_detection(self):
        prompt = build_transcription_prompt(quality="accurate")

        self.assertIn("LANGUAGE AND SCRIPT PRESERVATION - MANDATORY", prompt)
        self.assertIn("Detect and render each language span independently", prompt)
        self.assertIn("[Speaker 1]: வணக்கம் ආයුබෝවන් Your Honor", prompt)
        self.assertIn("FINAL NATIVE-SCRIPT CHECK", prompt)

    def test_correction_prompts_also_preserve_native_scripts(self):
        for prompt in (TRANSCRIPT_COVERAGE_AUDIT_PROMPT, TRANSCRIPT_REPETITION_REPAIR_PROMPT):
            self.assertIn("Never transliterate Tamil speech into Sinhala letters", prompt)
            self.assertIn("use [unclear] instead of the wrong script", prompt)

    def test_initial_transcription_request_includes_native_script_system_instruction(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl") as tmp:
            path = Path(tmp.name)
        try:
            build_transcription_jsonl(
                ["gs://input-bucket/uploads/manual/hearing.flac"],
                str(path),
                quality="accurate",
            )
            request = json.loads(path.read_text(encoding="utf-8").strip())["request"]

            self.assertIn("systemInstruction", request)
            self.assertIn(
                "Never transliterate Tamil speech into Sinhala letters",
                request["systemInstruction"]["parts"][0]["text"],
            )
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
