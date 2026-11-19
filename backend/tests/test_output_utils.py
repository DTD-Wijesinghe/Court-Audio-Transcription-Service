import unittest

from court_transcription.output_utils import _lookup_result_text, remove_repetition_loops


class RemoveRepetitionLoopsTests(unittest.TestCase):
    def test_result_lookup_accepts_same_stem_with_different_index(self):
        results = {
            "5|hearing_audio|transcript": "full transcript text",
        }

        self.assertEqual(
            _lookup_result_text(results, 0, "hearing_audio", "transcript"),
            "full transcript text",
        )

    def test_collapses_five_repeated_inline_phrases(self):
        phrase = "\u0d92\u0d9a\u0dad\u0dca \u0d91\u0d9a\u0dca\u0d9a \u0dad\u0db8\u0dba\u0dd2"
        text = "[Speaker 1]: " + ", ".join([phrase] * 5)

        cleaned, removed = remove_repetition_loops(text)

        self.assertEqual(cleaned, f"[Speaker 1]: {phrase}, [repeated lines]")
        self.assertEqual(removed, 4)

    def test_collapses_five_repeated_unclear_lines_across_speakers(self):
        text = "\n".join([
            "[Speaker 2]: [unclear]",
            "[Speaker 1]: [unclear]",
            "[Speaker 3]: [unclear]",
            "[Speaker 1]: [unclear]",
            "[Speaker 4]: [unclear]",
        ])

        cleaned, removed = remove_repetition_loops(text)

        self.assertEqual(cleaned, "[Speaker 2]: [unclear]\n[repeated lines]")
        self.assertEqual(removed, 4)

    def test_preserves_four_repeated_short_utterances(self):
        yes = "\u0d94\u0dc0\u0dca"
        text = "\n".join([
            f"[Speaker 1]: {yes}",
            f"[Speaker 2]: {yes}",
            f"[Speaker 1]: {yes}",
            f"[Speaker 2]: {yes}",
        ])

        cleaned, removed = remove_repetition_loops(text)

        self.assertEqual(cleaned, text)
        self.assertEqual(removed, 0)

    def test_collapses_repeated_multiline_model_block(self):
        block = [
            "[Judge]: Case number is called.",
            "[Lawyer 1]: I appear for the plaintiff.",
            "[Judge]: Proceed.",
        ]
        text = "\n".join(block * 3)

        cleaned, removed = remove_repetition_loops(text)

        self.assertEqual(cleaned, "\n".join([*block, "[repeated lines]"]))
        self.assertEqual(removed, 6)

    def test_collapses_repeated_role_label_utterance_loop(self):
        text = "\n".join(["[Judge]: The matter is postponed."] * 5)

        cleaned, removed = remove_repetition_loops(text)

        self.assertEqual(cleaned, "[Judge]: The matter is postponed.\n[repeated lines]")
        self.assertEqual(removed, 4)

    def test_can_remove_repetition_without_marker_for_final_output(self):
        text = "\n".join(["[Judge]: The matter is postponed."] * 5)

        cleaned, removed = remove_repetition_loops(text, repeat_marker="")

        self.assertEqual(cleaned, "[Judge]: The matter is postponed.")
        self.assertEqual(removed, 4)

    def test_collapses_space_separated_repeated_single_word(self):
        text = "[Speaker 1]: order order order order order order order"

        cleaned, removed = remove_repetition_loops(text)

        self.assertEqual(cleaned, "[Speaker 1]: order [repeated lines]")
        self.assertEqual(removed, 6)

    def test_collapses_space_separated_repeated_phrase(self):
        text = "[Speaker 1]: next date next date next date next date next date"

        cleaned, removed = remove_repetition_loops(text)

        self.assertEqual(cleaned, "[Speaker 1]: next date [repeated lines]")
        self.assertEqual(removed, 8)

    def test_removes_space_repeated_phrase_without_marker_for_final_output(self):
        text = "[Speaker 1]: next date next date next date next date next date"

        cleaned, removed = remove_repetition_loops(text, repeat_marker="")

        self.assertEqual(cleaned, "[Speaker 1]: next date")
        self.assertEqual(removed, 8)

    def test_collapses_long_space_repeated_sentence(self):
        phrase = "The court will take this matter again after the lunch interval"
        text = "[Judge]: " + " ".join([phrase] * 3)

        cleaned, removed = remove_repetition_loops(text)

        self.assertEqual(cleaned, f"[Judge]: {phrase} [repeated lines]")
        self.assertEqual(removed, 22)

    def test_preserves_short_space_repeated_word(self):
        text = "[Speaker 1]: yes yes yes yes"

        cleaned, removed = remove_repetition_loops(text)

        self.assertEqual(cleaned, text)
        self.assertEqual(removed, 0)


if __name__ == "__main__":
    unittest.main()
