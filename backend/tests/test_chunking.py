import unittest

from court_transcription.chunking import (
    SKIPPED_AUDIO_SCHEME,
    SKIPPED_CHUNK_TRANSCRIPT,
    filename_stem_from_uri,
    inspect_audio_uri,
    is_skipped_audio_uri,
    merge_chunk_transcripts,
    normalize_chunk_time_range,
)


class ChunkTranscriptMergeTests(unittest.TestCase):
    def _chunk_uri(self, part: int, total: int, start: int, end: int) -> str:
        return (
            "gs://inputvoice/uploads/manual/court_audio/"
            f"chunk_{part:03d}_of_{total:03d}_{start:06d}-{end:06d}.flac"
        )

    def _transcripts_for(self, audio_uris: list[str]) -> dict[str, str]:
        unique_phrases = [
            "alpha bravo charlie delta echo foxtrot golf",
            "hotel india juliet kilo lima mike november",
            "oscar papa quebec romeo sierra tango uniform",
            "victor whiskey xray yankee zulu mercury venus",
            "earth mars jupiter saturn uranus neptune pluto",
            "red orange yellow green blue indigo violet pink",
            "circle square triangle pentagon hexagon octagon star",
            "one two three four five six seven eight nine",
            "monday tuesday wednesday thursday friday saturday sunday",
            "january february march april may june july august",
            "september october november december solstice equinox dawn",
        ]
        return {
            f"{idx}|{filename_stem_from_uri(uri)}|transcript": unique_phrases[idx % len(unique_phrases)]
            for idx, uri in enumerate(audio_uris)
        }

    def test_merge_repairs_bad_final_chunk_end_from_old_uploads(self):
        audio_uris = [
            self._chunk_uri(part, 11, (part - 1) * 600, part * 600)
            for part in range(1, 11)
        ]
        audio_uris.append(self._chunk_uri(11, 11, 6000, 12157))

        parent_uris, results = merge_chunk_transcripts(audio_uris, self._transcripts_for(audio_uris))

        self.assertEqual(parent_uris, ["gs://inputvoice/uploads/manual/court_audio.flac"])
        merged = results["0|court_audio|transcript"]
        self.assertIn("alpha bravo", merged)        
        self.assertIn("september october", merged)

    def test_final_chunk_range_uses_inferred_total_duration(self):
        self.assertEqual(normalize_chunk_time_range(6, 6, 3000, 6232), (3000, 3232))
        self.assertEqual(normalize_chunk_time_range(5, 5, 2400, 5261), (2400, 2861))

    def test_non_final_chunk_range_is_clamped_to_ten_minutes(self):
        self.assertEqual(normalize_chunk_time_range(5, 8, 2400, 5261), (2400, 3000))

    def test_chunk_uri_parser_accepts_large_chunk_counts_and_long_seconds(self):
        item = inspect_audio_uri(
            "gs://bucket/uploads/very_long/chunk_1000_of_1200_599400-600000.flac",
            0,
        )

        self.assertTrue(item.is_chunk)
        self.assertEqual(item.chunk_number, 1000)
        self.assertEqual(item.total_chunks, 1200)
        self.assertEqual(item.section_start_seconds, 599400)
        self.assertEqual(item.section_end_seconds, 600000)

    def test_skipped_chunk_uri_merges_with_uploaded_chunks(self):
        audio_uris = [
            self._chunk_uri(1, 3, 0, 600),
            f"{SKIPPED_AUDIO_SCHEME}inputvoice/uploads/manual/court_audio/chunk_002_of_003_000600-001200.flac",
            self._chunk_uri(3, 3, 1200, 1800),
        ]
        transcripts = self._transcripts_for(audio_uris)
        transcripts["1|chunk_002_of_003_000600-001200|transcript"] = SKIPPED_CHUNK_TRANSCRIPT

        parent_uris, results = merge_chunk_transcripts(audio_uris, transcripts)

        self.assertTrue(is_skipped_audio_uri(audio_uris[1]))
        self.assertEqual(parent_uris, ["gs://inputvoice/uploads/manual/court_audio.flac"])
        merged = results["0|court_audio|transcript"]
        self.assertIn(SKIPPED_CHUNK_TRANSCRIPT, merged)  
        self.assertIn("alpha bravo", merged)              
        self.assertIn("oscar papa", merged) 

    def test_merge_accepts_chunk_transcript_with_different_index(self):
        audio_uris = [self._chunk_uri(1, 1, 0, 120)]
        transcripts = {
            "7|chunk_001_of_001_000000-000120|transcript": "recovered chunk text"
        }

        parent_uris, results = merge_chunk_transcripts(audio_uris, transcripts)

        self.assertEqual(parent_uris, ["gs://inputvoice/uploads/manual/court_audio.flac"])
        self.assertIn("recovered chunk text", results["0|court_audio|transcript"])


if __name__ == "__main__":
    unittest.main()
