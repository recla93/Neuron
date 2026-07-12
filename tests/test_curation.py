"""Curation gate tests (T54) — pure stdlib."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neuron.curation import _dup_key, curation_note, vet_keywords  # noqa: E402


class TestVetKeywords(unittest.TestCase):
    def test_good_keywords_pass_untouched(self):
        acc, notes = vet_keywords(["retry backoff", "install manifest", "Hebbian"])
        self.assertEqual(acc, ["retry backoff", "install manifest", "Hebbian"])
        self.assertEqual(notes, [])

    def test_verbs_dropped_with_note(self):
        acc, notes = vet_keywords(["implementare", "fix", "retry backoff"])
        self.assertEqual(acc, ["retry backoff"])
        self.assertEqual(len(notes), 2)
        self.assertIn("verbs aren't concepts", notes[0])

    def test_leading_verb_salvages_noun(self):
        acc, notes = vet_keywords(["fix retry backoff"])
        self.assertEqual(acc, ["retry backoff"])
        self.assertTrue(any("dropped the leading verb" in n for n in notes))

    def test_phrases_and_paths_dropped(self):
        acc, notes = vet_keywords(
            ["questa frase è chiaramente troppo lunga per un concetto",
             "src/neuron/server.py", "config.toml", "salience"])
        self.assertEqual(acc, ["salience"])
        self.assertEqual(len(notes), 3)

    def test_near_duplicate_remapped_to_existing(self):
        existing = {_dup_key("db layer"): "db layer"}
        acc, notes = vet_keywords(["DB Layers", "salience"], existing)
        self.assertEqual(acc, ["db layer", "salience"])
        self.assertTrue(any("existing concept 'db layer'" in n for n in notes))

    def test_accent_fold_duplicate(self):
        existing = {_dup_key("città"): "città"}
        acc, _ = vet_keywords(["Citta"], existing)
        self.assertEqual(acc, ["città"])

    def test_intra_turn_duplicates_collapse_silently(self):
        acc, notes = vet_keywords(["salience", "Salience", "saliences"])
        self.assertEqual(acc, ["salience"])
        self.assertEqual(notes, [])

    def test_all_dropped_returns_empty(self):
        acc, notes = vet_keywords(["fix", "implementare"])
        self.assertEqual(acc, [])
        self.assertEqual(len(notes), 2)

    def test_hardware_not_flagged_as_verb(self):
        # suffix-based verb detection would eat 'hardware'/'software'
        acc, notes = vet_keywords(["hardware", "software"])
        self.assertEqual(acc, ["hardware", "software"])
        self.assertEqual(notes, [])

    def test_note_block_compact(self):
        s = curation_note(["a", "b", "c", "d"])
        self.assertTrue(s.startswith("\ncuration: "))
        self.assertIn("(+1 more)", s)
        self.assertEqual(curation_note([]), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
