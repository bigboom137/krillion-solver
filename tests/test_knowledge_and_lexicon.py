"""Built-in knowledge, ranking with checks, history edits and training export."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import krillion_solver as ks
import local_knowledge as lk
import prompt_rules as pr
import winspell

HAVE_WORDS = bool(lk.load_words())
HAVE_SPELL = winspell.is_word("kitchen") is not None


class Classify(unittest.TestCase):
    MATCH = [
        ("Name a country starting with B", "country"),
        ("Name a country whose English name contains J, K, or V", "country"),
        ('Name a country with "in" in its name', "country"),
        ("Name a European country", "country"),
        ("Name a landlocked country in Africa", "country"),
        ("Name a fruit (apple doesn't count)", "fruit"),
        ("Name an animal with stripes", "animal"),
        ("Name a striped animal", "animal"),
        ('Name an English word containing "itch"', "word"),
        ("Name a three-syllable word starting with B", "word"),
        ("Name a musical instrument", "instrument"),
        ("Name a character from SpongeBob", "spongebob_character"),
        ("Name a planet other than Earth", "planet"),
    ]
    NO_MATCH = [
        "Name an animal that can run faster than a human",
        "Name a country that shares a land border with at least four other countries",
        "Name a fruit that is red", "Name a type of dress", "Name a dish containing cheese",
        "Name a god from mythology (Hindu, Greek, Roman, Norse or Egyptian)",
    ]

    def test_matches(self):
        for prompt, kind in self.MATCH:
            with self.subTest(prompt=prompt):
                self.assertEqual((lk.classify(prompt) or (None,))[0], kind)

    def test_unknown_qualifiers_are_left_to_the_model(self):
        for prompt in self.NO_MATCH:
            with self.subTest(prompt=prompt):
                self.assertIsNone(lk.classify(prompt))
                self.assertEqual(lk.answers(prompt), [])

    def test_country_answers_obey_rules_generated(self):
        # Every starting letter that has countries: all built-in answers must fit.
        for letter in sorted({name[0] for name in lk.COUNTRIES}):
            prompt = f"Name a country starting with {letter}"
            with self.subTest(prompt=prompt):
                found = lk.answers(prompt)
                self.assertTrue(found)
                for answer, *_ in found:
                    self.assertTrue(answer.startswith(letter), answer)
                    self.assertIn(answer, lk.COUNTRIES)

    def test_regions_and_landlocked(self):
        for answer, *_ in lk.answers("Name a landlocked country in Africa", limit=30):
            self.assertEqual(lk.COUNTRIES[answer], ("africa", True))
        for answer, *_ in lk.answers("Name a European country", limit=60):
            self.assertEqual(lk.COUNTRIES[answer][0], "europe")

    def test_made_up_countries_are_doubted(self):
        p = "Name a country starting with B"
        for fake in ("British Bay", "Burkina Beach", "Bhutanese Islands", "Belarusian Exclave"):
            self.assertEqual(lk.check(p, fake), (None, "not a country I know"), fake)
        for real in ("Bolivia", "Brunei Darussalam", "Burma", "the Bahamas", "Belgium"):
            self.assertEqual(lk.check(p, real), (None, None), real)

    def test_exclusions_respected(self):
        self.assertNotIn("Apple", [a for a, *_ in lk.answers("Name a fruit (apple doesn't count)", limit=50)])
        self.assertNotIn("Earth", [a for a, *_ in lk.answers("Name a planet other than Earth", limit=50)])


@unittest.skipUnless(HAVE_WORDS, "cmudict isn't installed")
class Words(unittest.TestCase):
    def test_dictionary_answers_obey_rules(self):
        for prompt in ('Name an English word containing "itch"', "Name a word ending in -ology",
                       "Name a 5-letter word", "Name a three-syllable word starting with B"):
            rules = pr.parse(prompt)
            found = lk.answers(prompt)
            with self.subTest(prompt=prompt):
                self.assertTrue(found)
                for word, *_ in found:
                    self.assertIsNone(rules.violation(word), word)

    @unittest.skipUnless(HAVE_SPELL, "Windows spell checker unavailable")
    def test_no_surnames(self):
        for word, *_ in lk.answers('Name an English word containing "itch"', limit=20):
            self.assertTrue(winspell.is_word(word), word)

    def test_checks(self):
        p = "Name a three-syllable word starting with B"
        self.assertEqual(lk.check(p, "Banana"), (None, None))
        self.assertEqual(lk.check(p, "Butter")[0], "has 2 syllables, not 3")
        if HAVE_SPELL:
            self.assertEqual(lk.check('Name an English word containing "itch"', "itchoo")[1], "not in the dictionary")


class Lexicon(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.lex = ks.Lexicon(os.path.join(self.dir.name, "lexicon.json"))

    def tearDown(self):
        self.dir.cleanup()

    def test_rule_breakers_rank_last_and_accepted_is_trusted(self):
        p = 'Name a country that ends in "stan"'
        self.lex.add(p, "Pakistani", "rare", 0.9, "ollama:test")
        self.lex.add(p, "Kazakhstan", "schooler", 0.9, "ollama:test")
        self.lex.add(p, "Uzbek", "rare", 0.9, "ollama:test")
        self.assertEqual([a["answer"] for a in self.lex.ranked(p)], ["Kazakhstan"])
        everything = self.lex.ranked(p, include_rejected=True)
        self.assertEqual(everything[0]["answer"], "Kazakhstan")
        self.assertTrue(all(a["violation"] for a in everything[1:]))
        self.lex.mark(p, "Uzbek", "deep_cut")  # the game said yes: believe it over our parser
        self.assertIn("Uzbek", [a["answer"] for a in self.lex.ranked(p)])

    def test_local_answers_dont_count_as_model_answers(self):
        p = "Name a country starting with B"
        self.assertGreater(self.lex.add_local(p), 0)
        self.assertEqual(self.lex.model_count(p), 0)
        self.assertEqual(self.lex.add_local(p), 0)  # only once per prompt
        self.lex.add(p, "Bhutan2", "rare", 0.5, "ollama:test")
        self.assertEqual(self.lex.model_count(p), 1)

    def test_edits(self):
        p = "Name a fruit"
        self.lex.add(p, "Banan", "plankton", 0.9, "ollama:test")
        self.assertTrue(self.lex.rename(p, "Banan", "Banana"))
        self.lex.mark(p, "Banana", "plankton")
        self.lex.reset(p, "Banana")
        self.assertEqual(self.lex.ranked(p)[0]["status"], "unverified")
        self.lex.delete(p, "Banana")
        self.assertEqual(self.lex.ranked(p), [])
        self.lex.delete(p)
        self.assertIsNone(self.lex.find(p))

    def test_export_training(self):
        p = "Name a fruit"
        self.lex.add(p, "Rambutan", "rare", 0.9, "ollama:test")
        self.lex.add(p, "Fakefruit", "rare", 0.9, "ollama:test")
        self.lex.mark(p, "Rambutan", "rare")
        self.lex.mark(p, "Fakefruit", rejected=True)
        examples, rejected = ks.export_training(self.lex, self.dir.name)
        self.assertEqual((examples, rejected), (1, 1))
        with open(os.path.join(self.dir.name, "training_examples.jsonl"), encoding="utf-8") as f:
            row = json.loads(f.readline())
        roles = [m["role"] for m in row["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant"])
        reply = json.loads(row["messages"][2]["content"])
        self.assertEqual(reply["answers"][0]["answer"], "Rambutan")


if __name__ == "__main__":
    unittest.main()
