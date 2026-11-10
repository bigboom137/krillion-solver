"""Prompt rules, OCR prompt matching and OCR text cleanup."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import krillion_solver as ks
import prompt_rules as pr
import screen_reader as sr


class PromptRules(unittest.TestCase):
    CASES = [
        # prompt, answer, allowed?
        ('Name an English word containing "itch"', "petch", False),
        ('Name an English word containing "itch"', "Kitchen", True),
        ("Name a country starting with B", "Brazil", True),
        ("Name a country starting with B", "Chile", False),
        ("Name a band starting with B", "The Beatles", True),
        ('Name a country that ends in "stan"', "Kazakhstan", True),
        ('Name a country that ends in "stan"', "Pakistani", False),
        ("Name a word ending in -ing", "Running", True),
        ("Name a word ending in -ing", "Runner", False),
        ("Name a 5-letter word", "Apple", True),
        ("Name a 5-letter word", "Banana", False),
        ("Name a three-letter animal", "Cat", True),
        ("Name a word with 4 letters", "Moon", True),
        ("Name a country with the letter Z", "Peru", False),
        ("Name a country whose English name contains J, K, or V", "Vanuatu", True),
        ("Name a country whose English name contains J, K, or V", "Peru", False),
        ('Name a word ending in "a" or "o"', "Pizza", True),
        ("Name a country starting with B and ending with A", "Bolivia", True),
        ("Name a country starting with B and ending with A", "Brazil", False),
        ("Name a fruit (apple doesn't count)", "Apple", False),
        ("Name a planet other than Earth", "Earth", False),
        ("Name a color, but red and blue don't count", "Blue", False),
        ("Name a color, but red and blue don't count", "Green", True),
        # Bare words as targets
        ("Name a English word ending in bound", "Spellbound", True),
        ("Name a English word ending in bound", "Spellbinding", False),
        ("Name an English word containing micro", "Microwave", True),
        ("Name an English word containing micro", "Kitchen", False),
        ("Name a word ending in ing or ed", "Jumped", True),
        ("Name a word ending in ing or ed", "Jumps", False),
        ("Name a country starting with San or Saint", "Saint Lucia", True),
        ("Name a country starting with San or Saint", "Samoa", False),
        # Negated rules
        ('Name an adverb that doesn\'t end in ly (like "soon")', "Quickly", False),
        ('Name an adverb that doesn\'t end in ly (like "soon")', "Often", True),
        ("Name an animal that does not start with S", "Snake", False),
        ("Name an animal that does not start with S", "Okapi", True),
        ("Name a word that doesn't contain E", "Banana", True),
        ("Name a word that doesn't contain E", "Apple", False),
    ]
    NO_RULES = [
        "Name a dish containing cheese", "Name a movie starting with a number", "Name a song by T-Pain",
        "Name a word that starts with a vowel", "Name a fruit", "Name a type of balloon (it's my birthday)",
        "Name a dish containing cheese or ham", "Name something you do with friends", "Name a word with friends",
        "Name a food ending in a vowel", "Name a word starting with it",
        "Name a country that shares a land border with France, Germany, or Spain",
        "Name a movie from Pixar, DreamWorks Animation, Illumination, Disney Animation, Sony Pictures Animation "
        "or Blue Sky Studios",
    ]

    def test_cases(self):
        for prompt, answer, allowed in self.CASES:
            with self.subTest(prompt=prompt, answer=answer):
                self.assertEqual(pr.parse(prompt).violation(answer) is None, allowed)

    def test_ambiguous_prompts_get_no_rules(self):
        for prompt in self.NO_RULES:
            with self.subTest(prompt=prompt):
                self.assertFalse(pr.parse(prompt), pr.parse(prompt).describe())

    def test_without_rules(self):
        self.assertEqual(pr.without_rules("Name a country starting with B").strip(), "Name a country")
        self.assertEqual(pr.without_rules("Name a fruit (apple doesn't count)").strip(" )"), "Name a fruit")

    def test_generated_letters(self):
        # Every letter, three phrasings, a matching and a non-matching answer: 156 cases.
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            other = "Q" if letter != "Q" else "Z"
            for phrasing in ("starting with {}", "that starts with {}", "beginning with the letter {}"):
                rules = pr.parse(f"Name an animal {phrasing.format(letter)}")
                with self.subTest(letter=letter, phrasing=phrasing):
                    self.assertIsNone(rules.violation(f"{letter}xample"))
                    self.assertIsNotNone(rules.violation(f"{other}xample"))


class OcrMatching(unittest.TestCase):
    def test_misreads_match(self):
        for a, b in (('Name an English word containing "itch"', 'Name an Eng1ish word contaIning "ltch"'),
                     ("Name a witch or wizard from a movie", "Name a witch or wizardfrom a m0vie"),
                     ("Name a milk-based drink", "Name a mi1k based drink!")):
            self.assertEqual(ks.ocr_key(a), ks.ocr_key(b))

    def test_real_differences_dont(self):
        for a, b in (('Name an English word containing "micro"', 'Name an English word containing "itch"'),
                     ('containing "micro"', 'containing "macro"'),
                     ("Name a witch or wizard from a movie", "Name a witch or wizard from a book")):
            self.assertNotEqual(ks.ocr_key(a), ks.ocr_key(b))


class OcrText(unittest.TestCase):
    def test_extract_prompt(self):
        cases = [
            (["On", "PROMPT 1 OF 7", "Name a noodle dish", "rarer • i nk", "descending ."], "Name a noodle dish"),
            (["Name a fruit", "V any answer passes the puffer V"], "Name a fruit"),
            (["Name a fruit", "v any answer passes the puffer"], "Name a fruit"),
            (["Name a type of", "tropical fruit", "▼ rarer answers sink deeper ▼"], "Name a type of tropical fruit"),
        ]
        for lines, want in cases:
            self.assertEqual(sr.extract_prompt(lines), want)

    def test_custom_ignore(self):
        self.assertEqual(sr.extract_prompt(["Name a vegetable", "only rare answers count"],
                                           ignore=["rare answers count"]), "Name a vegetable")

    def test_group_lines_reorders_fragments(self):
        words = [(10, 100, 20, "Name"), (70, 100, 20, "a"), (90, 100, 20, "movie"), (10, 130, 20, "Animation,"),
                 (10, 220, 12, "rarer"), (120, 130, 20, "Illumination"), (10, 160, 20, "Studios")]
        self.assertEqual(sr.group_lines(words),
                         ["Name a movie", "Animation, Illumination", "Studios", "rarer"])


class PartialJson(unittest.TestCase):
    def test_complete_answers_only(self):
        text = '{"answers":[{"answer":"Cheetah","tier":"plankton","confidence":0.99},{"answer":"Prong'
        self.assertEqual([a["answer"] for a in ks._complete_answers(text)], ["Cheetah"])
        self.assertEqual(ks._complete_answers('{"answ'), [])


if __name__ == "__main__":
    unittest.main()
