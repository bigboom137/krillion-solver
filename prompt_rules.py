"""Rules a prompt states about its answers, used to drop answers that break them.

Covers spelling rules ("starts with B", "ending in -stan", 'containing "itch"',
"a 5-letter word") and exclusions ("(apple doesn't count)", "other than red").
Parsing is deliberately conservative: a rule is only applied when its target is
unambiguous (quoted, a capital letter, "the letter x", or a "-suffix"), so a
prompt like "Name a dish containing cheese" is never treated as a spelling rule.
"""

import re
import unicodedata
from dataclasses import dataclass, field

_QUOTES = "\"'“”‘’`"
# What a spelling rule applies to: 'quoted text', the letter(s) x, -suffix, or a lone capital letter.
# Keywords are case-insensitive via (?i:...); the lone capital letter must stay case-sensitive.
_TARGET = (rf"(?:[{_QUOTES}](?P<quoted>[^{_QUOTES}]{{1,20}})[{_QUOTES}]"
           r"|(?i:the\s+letters?)\s+(?P<letter>[A-Za-z]{1,15})\b"
           r"|-(?P<suffix>[A-Za-z]{1,15})\b"
           r"|(?<![\w'])(?P<capital>[A-Z])(?![\w'.-]))")
# Further alternatives right after a target: 'contains J, K, or V', 'ends in "a" or "o"'.
_ALTERNATIVE = re.compile(r"(?:\s*,\s*(?i:or\s+)?|\s+(?i:or)\s+|\s*/\s*)" + _TARGET)
_STARTS = re.compile(r"\b(?i:start(?:s|ing)?|begin(?:s|ning)?)\s+(?i:with|in)\s+" + _TARGET)
_ENDS = re.compile(r"\b(?i:end(?:s|ing)?)\s+(?i:with|in)\s+" + _TARGET)
_CONTAINS = re.compile(r"\b(?i:contain(?:s|ing)?|includ(?:es|ing)|with)\s+" + _TARGET)
_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_COUNT = r"(\d{1,2}|" + "|".join(_NUMBERS) + r")"
# "5-letter word", "five letter animal", "a word with 5 letters"; not "with two letters 'o'".
_LENGTH = (re.compile(rf"\b{_COUNT}[\s-]+letters?\b(?!\s*[{_QUOTES}])", re.I),
           re.compile(rf"\bwith\s+(?:exactly\s+)?{_COUNT}\s+letters\b(?!\s*[{_QUOTES}])", re.I))
_NOT_COUNTING = r"(?:doesn'?t|does\s+not|don'?t|do\s+not|isn'?t|is\s+not|won'?t)\s+count"
_EXCLUDED = (
    re.compile(rf"[(,;—–-]\s*(?:but\s+)?(?P<items>[^(),;—–]+?)\s+{_NOT_COUNTING}", re.I),
    re.compile(r"\b(?:excluding|except(?:\s+for)?|other\s+than|besides|not\s+counting|but\s+not)\s+"
               r"(?P<items>[^.;:()!?]+)", re.I),
)


def _fold(text):
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _letters(text):
    return re.sub(r"[^a-z0-9]", "", _fold(text))


def _item_key(text):
    key = re.sub(r"[^a-z0-9 ]+", " ", _fold(text))
    key = re.sub(r"\s+", " ", key).strip()
    return re.sub(r"^(?:the|a|an) ", "", key)


def _target(match):
    value = next(v for v in (match.group("quoted"), match.group("letter"),
                             match.group("suffix"), match.group("capital")) if v)
    return _letters(value)


def _either(options):
    return " or ".join(f"“{o}”" for o in options)


@dataclass
class Rules:
    # Each spelling rule is a list of alternatives; an answer needs to satisfy one of them.
    starts: list = field(default_factory=list)
    ends: list = field(default_factory=list)
    contains: list = field(default_factory=list)
    length: int = None
    excluded: set = field(default_factory=set)
    spans: list = field(default_factory=list)  # (start, end) of each rule's wording in the prompt

    def __bool__(self):
        return bool(self.starts or self.ends or self.contains or self.length or self.excluded)

    def violation(self, answer):
        """Why answer breaks a rule (a short phrase), or None if it's fine."""
        letters = _letters(answer)
        without_article = _letters(re.sub(r"^\s*(?:the|a|an)\s+", "", answer, flags=re.I))
        if _item_key(answer) in self.excluded:
            return "excluded by the prompt"
        for options in self.starts:
            if not any(letters.startswith(s) or without_article.startswith(s) for s in options):
                return f"doesn't start with {_either(options)}"
        for options in self.ends:
            if not any(letters.endswith(e) for e in options):
                return f"doesn't end with {_either(options)}"
        for options in self.contains:
            if not any(c in letters for c in options):
                return f"doesn't contain {_either(options)}"
        if self.length and len(re.sub(r"[^a-z]", "", _fold(answer))) != self.length:
            return f"isn't {self.length} letters"
        return None

    def describe(self):
        parts = [f"starts with {_either(o)}" for o in self.starts]
        parts += [f"ends with {_either(o)}" for o in self.ends]
        parts += [f"contains {_either(o)}" for o in self.contains]
        if self.length:
            parts.append(f"{self.length} letters")
        if self.excluded:
            parts.append("not " + ", ".join(sorted(self.excluded)))
        return " · ".join(parts)


def without_rules(prompt, rules=None):
    """The prompt with its rule wording cut out: 'Name a country starting with B' -> 'Name a country'."""
    rules = rules or parse(prompt)
    keep, last = [], 0
    for start, end in sorted(rules.spans):
        if start >= last:
            keep.append(prompt[last:start])
            last = end
    keep.append(prompt[last:])
    return "".join(keep)


def parse(prompt):
    """Rules stated in a prompt (an empty Rules when there are none)."""
    rules = Rules()
    taken = []  # spans already used, so "starts with" isn't read again as "with"
    for pattern, bucket in ((_STARTS, rules.starts), (_ENDS, rules.ends), (_CONTAINS, rules.contains)):
        for m in pattern.finditer(prompt):
            if any(a <= m.start() < b for a, b in taken):
                continue
            options, end = [_target(m)], m.end()
            while (more := _ALTERNATIVE.match(prompt, end)):
                options.append(_target(more))
                end = more.end()
            options = [o for o in options if o]
            if options:
                bucket.append(options)
                taken.append((m.start(), end))
    rules.spans.extend(taken)
    for pattern in _LENGTH:
        m = pattern.search(prompt)
        if m:
            n = m.group(1).lower()
            rules.length = int(n) if n.isdigit() else _NUMBERS.get(n)
            rules.spans.append(m.span())
            break
    for pattern in _EXCLUDED:
        for m in pattern.finditer(prompt):
            rules.spans.append(m.span())
            for item in re.split(r",|\bor\b|\band\b|\bnor\b", m.group("items"), flags=re.I):
                key = _item_key(item.strip(" " + _QUOTES))
                if key:
                    rules.excluded.add(key)
    return rules
