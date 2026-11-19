import json
import tempfile
import unittest
from pathlib import Path

from court_transcription.batch_jobs import (
    build_transcription_jsonl,
    is_frequency_penalty_validation_error,
)
from court_transcription.config import TRANSCRIPTION_MAX_OUTPUT_TOKENS, TRANSCRIPTION_TOP_P
from court_transcription.prompts import TRANSCRIPTION_SYSTEM_INSTRUCTION


class TranscriptionBatchJsonlTests(unittest.TestCase):
    def _build_single_entry(self) -> dict:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        tmp.close()
        path = Path(tmp.name)
        try:
            entries = build_transcription_jsonl(
                ["gs://input-bucket/uploads/manual/full_hearing.flac"],
                str(path),
                quality="accurate",
            )
            self.assertEqual(entries, 1)
            return json.loads(path.read_text(encoding="utf-8").strip())
        finally:
            path.unlink(missing_ok=True)

    def test_transcription_jsonl_uses_literal_system_instruction_and_deterministic_config(self):
        entry = self._build_single_entry()
        request = entry["request"]

        self.assertEqual(
            request["systemInstruction"]["parts"][0]["text"],
            TRANSCRIPTION_SYSTEM_INSTRUCTION.strip(),
        )
        self.assertEqual(
            request["generationConfig"],
            {
                "temperature": 0.0,
                "topP": TRANSCRIPTION_TOP_P,
                "maxOutputTokens": TRANSCRIPTION_MAX_OUTPUT_TOKENS,
            },
        )

        user_parts = request["contents"][0]["parts"]
        self.assertEqual(
            user_parts[0]["fileData"]["fileUri"],
            "gs://input-bucket/uploads/manual/full_hearing.flac",
        )
        self.assertIn("WHOLE AUDIO RULES", user_parts[1]["text"])
        self.assertNotIn("CHUNKED AUDIO RULES", user_parts[1]["text"])

    def test_transcription_jsonl_uses_chunk_prompt_for_chunk_uri(self):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        tmp.close()
        path = Path(tmp.name)
        try:
            entries = build_transcription_jsonl(
                [
                    "gs://input-bucket/uploads/manual/hearing/"
                    "chunk_001_of_002_000000-000600.flac"
                ],
                str(path),
                quality="accurate",
            )
            self.assertEqual(entries, 1)
            entry = json.loads(path.read_text(encoding="utf-8").strip())
        finally:
            path.unlink(missing_ok=True)

        user_prompt = entry["request"]["contents"][0]["parts"][1]["text"]
        self.assertIn("CHUNKED AUDIO RULES", user_prompt)
        self.assertNotIn("WHOLE AUDIO RULES", user_prompt)

    def test_frequency_penalty_validation_error_detector(self):
        self.assertTrue(
            is_frequency_penalty_validation_error(
                ValueError("Invalid field frequencyPenalty for Gemini 3.6 Flash")
            )
        )
        self.assertFalse(is_frequency_penalty_validation_error(ValueError("model not found")))


if __name__ == "__main__":
    unittest.main()
