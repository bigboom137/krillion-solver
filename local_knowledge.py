"""Built-in knowledge: English words and hand-written category lists.

Used three ways:
  * instant answers that appear before the model has written anything,
  * answers when Ollama isn't running,
  * checks on the model's answers: for "word" prompts, whether the word exists, and
    syllable counts ("a 3-syllable word").

The word list is the CMU Pronouncing Dictionary from the optional `cmudict` package
(py -m pip install cmudict). Without it, everything here except word and syllable
features still works.

Category lists only apply when the prompt is just the category plus rules we
understand ("Name a country starting with B", "Name a European country", "Name an
animal with stripes"). Any other qualifier ("an animal that can run faster than a
human") means the list could suggest wrong answers, so the model handles it alone.
"""

import re
import threading
import unicodedata
import zlib

import prompt_rules
import winspell

# --- word list -----------------------------------------------------------------

_words = None           # {word: {syllable counts}} once loaded
_loading = threading.Lock()


def load_words():
    """Load the CMU dictionary (about a second); returns it, or {} if cmudict isn't installed."""
    global _words
    with _loading:
        if _words is None:
            try:
                import cmudict
                entries = cmudict.dict()
            except Exception:
                entries = {}
            _words = {}
            for word, prons in entries.items():
                if word.isalpha():
                    _words[word] = {sum(ph[-1].isdigit() for ph in pron) for pron in prons}
    return _words


def start_loading():
    threading.Thread(target=load_words, daemon=True).start()


def words(wait=True):
    """The word list, or None while it's still loading in the background (when wait is False)."""
    if _words is not None or wait:
        return load_words()
    return None


def have_dictionary():
    return bool(words(wait=False))


def is_real_word(word, dictionary=None):
    """True/False, or None if we can't tell. Windows' spell checker decides when it's
    available (it rejects the surnames and places that fill much of the CMU list);
    otherwise being in the CMU dictionary counts."""
    verdict = winspell.is_word(word)
    if verdict is not None:
        return verdict
    if dictionary:
        return word in dictionary
    return None


# --- categories ------------------------------------------------------------------
# "common" answers are ones most players think of; "rare" ones are correct but less obvious.

def _items(text):
    return [s.strip() for s in re.split(r"[,\n]", text) if s.strip()]


CATEGORIES = {
    "fruit": (r"fruits?", {
        "common": "Apple, Banana, Orange, Grape, Strawberry, Mango, Pineapple, Watermelon, Peach, Pear, Cherry, Lemon, Kiwi, Blueberry",
        "rare": "Rambutan, Mangosteen, Durian, Lychee, Persimmon, Quince, Kumquat, Feijoa, Jabuticaba, Salak, Cherimoya, "
                "Soursop, Tamarillo, Longan, Loquat, Medlar, Pawpaw, Ugli fruit, Jackfruit, Physalis, Gooseberry, Elderberry",
    }),
    "vegetable": (r"vegetables?|veggies?", {
        "common": "Carrot, Broccoli, Potato, Onion, Lettuce, Spinach, Cucumber, Pepper, Cabbage, Corn, Peas, Celery",
        "rare": "Kohlrabi, Salsify, Celeriac, Romanesco, Jicama, Fennel, Rutabaga, Parsnip, Okra, Bok choy, "
                "Daikon, Chard, Sunchoke, Burdock, Scorzonera, Fiddlehead, Samphire, Kale, Leek, Artichoke",
    }),
    "sport": (r"sports?", {
        "common": "Soccer, Basketball, Baseball, Tennis, Football, Golf, Hockey, Volleyball, Swimming, Boxing, Cricket, Rugby",
        "rare": "Sepak takraw, Hurling, Kabaddi, Jai alai, Curling, Lacrosse, Handball, Fencing, Bandy, Korfball, "
                "Pickleball, Water polo, Biathlon, Bobsleigh, Skeleton, Underwater hockey, Pelota, Shinty",
    }),
    "instrument": (r"(?:musical )?instruments?", {
        "common": "Piano, Guitar, Violin, Drums, Flute, Trumpet, Saxophone, Cello, Clarinet, Harp, Ukulele",
        "rare": "Theremin, Hurdy-gurdy, Didgeridoo, Bassoon, Oboe, Sitar, Bouzouki, Glockenspiel, Kalimba, Ocarina, "
                "Harpsichord, Dulcimer, Balalaika, Shamisen, Erhu, Celesta, Contrabassoon, Euphonium, Sousaphone, Zither",
    }),
    "occupation": (r"jobs?|occupations?|professions?|careers?", {
        "common": "Doctor, Teacher, Nurse, Lawyer, Engineer, Firefighter, Police officer, Chef, Farmer, Pilot, Dentist",
        "rare": "Cartographer, Farrier, Sommelier, Actuary, Luthier, Taxidermist, Cooper, Chandler, Glazier, Ombudsman, "
                "Lexicographer, Milliner, Cobbler, Horologist, Phlebotomist, Stenographer, Arborist, Wheelwright",
    }),
    "vehicle": (r"vehicles?|modes? of transport(?:ation)?", {
        "common": "Car, Bus, Truck, Bicycle, Motorcycle, Train, Airplane, Boat, Van, Taxi, Helicopter",
        "rare": "Rickshaw, Tuk-tuk, Hovercraft, Zeppelin, Funicular, Gondola, Segway, Sidecar, Snowmobile, Tandem, "
                "Unicycle, Velomobile, Monorail, Trolleybus, Sampan, Hydrofoil, Pedalo, Toboggan",
    }),
    "flower": (r"flowers?", {
        "common": "Rose, Tulip, Daisy, Sunflower, Lily, Orchid, Daffodil, Lavender, Carnation, Poppy",
        "rare": "Hellebore, Protea, Ranunculus, Alstroemeria, Gardenia, Freesia, Anemone, Camellia, Foxglove, Snapdragon, "
                "Larkspur, Edelweiss, Bougainvillea, Plumeria, Amaryllis, Hyacinth, Chrysanthemum, Begonia, Gerbera",
    }),
    "tree": (r"trees?", {
        "common": "Oak, Maple, Pine, Birch, Willow, Palm, Apple tree, Cherry tree, Spruce, Elm",
        "rare": "Baobab, Ginkgo, Sequoia, Hornbeam, Rowan, Linden, Alder, Sycamore, Larch, Juniper, Cypress, Hawthorn, "
                "Yew, Banyan, Jacaranda, Eucalyptus, Mahogany, Teak, Wollemi pine, Dragon blood tree",
    }),
    "planet": (r"planets?(?: in (?:our|the) solar system)?", {
        "common": "Earth, Mars, Jupiter, Saturn",
        "rare": "Mercury, Venus, Uranus, Neptune",
    }),
    "gemstone": (r"gemstones?|gems?|precious stones?", {
        "common": "Diamond, Ruby, Emerald, Sapphire, Amethyst, Pearl, Opal",
        "rare": "Tanzanite, Alexandrite, Tourmaline, Peridot, Garnet, Topaz, Aquamarine, Morganite, Spinel, Zircon, "
                "Jade, Lapis lazuli, Moonstone, Citrine, Turquoise, Onyx, Tsavorite, Kunzite",
    }),
    "board_game": (r"board games?", {
        "common": "Monopoly, Chess, Scrabble, Checkers, Clue, Risk, Sorry!, Battleship, Candy Land, Trivial Pursuit",
        "rare": "Catan, Carcassonne, Ticket to Ride, Pandemic, Azul, Go, Backgammon, Mancala, Wingspan, Agricola, "
                "Stratego, Othello, Parcheesi, Chinese checkers, Twilight Struggle, Dominion, Terraforming Mars",
    }),
    "dinosaur": (r"dinosaurs?", {
        "common": "Tyrannosaurus rex, Triceratops, Stegosaurus, Velociraptor, Brachiosaurus, Diplodocus, Spinosaurus",
        "rare": "Pachycephalosaurus, Parasaurolophus, Therizinosaurus, Carnotaurus, Ankylosaurus, Iguanodon, Allosaurus, "
                "Gallimimus, Dilophosaurus, Microraptor, Argentinosaurus, Deinonychus, Oviraptor, Baryonyx, Giganotosaurus",
    }),
    "body_part": (r"body parts?|parts? of the (?:human )?body", {
        "common": "Hand, Foot, Head, Arm, Leg, Eye, Nose, Mouth, Ear, Heart, Knee, Elbow",
        "rare": "Philtrum, Uvula, Clavicle, Patella, Sternum, Scapula, Coccyx, Septum, Earlobe, Cuticle, Pinky, "
                "Nape, Larynx, Pancreas, Spleen, Appendix, Tibia, Femur, Achilles tendon",
    }),
    "interjection": (r"interjections?|exclamations?", {
        "common": "Wow, Ouch, Oops, Hey, Yay, Oh, Hooray, Ah, Ugh, Yikes",
        "rare": "Egads, Zounds, Gadzooks, Huzzah, Crikey, Blimey, Golly, Gosh, Jeepers, Phooey, Alas, Eureka, Bravo, "
                "Whoopsie, Shucks, Fiddlesticks, Yippee, Tsk",
    }),
    "filler_word": (r"filler words?|fillers?|filler phrases?|hesitation words?", {
        "common": "Um, Uh, Like, So, Well, You know, Actually, Basically",
        "rare": "Er, Hmm, I mean, Literally, Right, Okay, Kind of, Sort of, Anyway, Whatever, Totally, Honestly",
    }),
    "sitcom": (r"sitcoms?|situation comed(?:y|ies)|sit-?down comed(?:y|ies)", {
        "common": "Friends, The Office, Seinfeld, The Big Bang Theory, How I Met Your Mother, Modern Family, Brooklyn Nine-Nine",
        "rare": "Arrested Development, Community, Parks and Recreation, Frasier, Cheers, Taxi, Fawlty Towers, "
                "The Good Place, Schitt's Creek, Abbott Elementary, Black Books, Spaced, Peep Show, 30 Rock, Scrubs, "
                "Malcolm in the Middle, Everybody Loves Raymond, The IT Crowd, Superstore, Happy Endings",
    }),
    "himym_character": (r"characters? (?:from|in|on) how i met your mother", {
        "common": "Ted Mosby, Barney Stinson, Robin Scherbatsky, Marshall Eriksen, Lily Aldrin",
        "rare": "Ranjit, Carl, Wendy the Waitress, Victoria, Stella, Zoey, Tracy McConnell, James Stinson, Patrice, "
                "Quinn, Nora, Kevin, Sandy Rivers, Mickey Aldrin, Judy Eriksen, Marvin Eriksen, Loretta Stinson",
    }),
    "spongebob_character": (r"characters? (?:from|in|on) sponge ?bob(?: square ?pants)?", {
        "common": "SpongeBob, Patrick Star, Squidward, Mr. Krabs, Sandy Cheeks, Plankton, Gary",
        "rare": "Pearl, Mrs. Puff, Larry the Lobster, Karen, Mermaid Man, Barnacle Boy, Flying Dutchman, Squilliam, "
                "Man Ray, Bubble Bass, Dirty Bubble, King Neptune, Old Man Jenkins, Patchy the Pirate, Painty the Pirate",
    }),
    "dish": (r"foods?|dish(?:es)?", {
        "common": "Pizza, Burger, Sushi, Pasta, Tacos, Fried chicken, Lasagna, Curry, Salad, Sandwich",
        "rare": "Shakshuka, Bibimbap, Moussaka, Ceviche, Pierogi, Poutine, Haggis, Bobotie, Feijoada, Khachapuri, "
                "Laksa, Rendang, Pho, Jollof rice, Injera, Goulash, Pupusa, Borscht, Okonomiyaki, Cassoulet",
    }),
}

# Animals, with traits for prompts like "an animal with stripes".
ANIMALS = {
    "common": "Dog, Cat, Lion, Tiger, Elephant, Giraffe, Horse, Cow, Monkey, Bear, Zebra, Dolphin, Penguin, Rabbit, Shark",
    "rare": "Okapi, Pangolin, Axolotl, Aye-aye, Quokka, Tapir, Saiga, Fossa, Numbat, Kinkajou, Binturong, Dugong, "
            "Narwhal, Markhor, Gharial, Shoebill, Capybara, Tarsier, Gerenuk, Solenodon, Takin, Coati",
}
ANIMAL_TRAITS = {
    "shell": ("shells?|shelled", "Turtle, Tortoise, Snail, Crab, Lobster, Hermit crab, Armadillo, Clam, Oyster, Nautilus, "
                                 "Scallop, Mussel, Barnacle, Conch, Abalone, Horseshoe crab"),
    "stripes": ("stripes?|striped", "Zebra, Tiger, Skunk, Okapi, Bongo, Numbat, Chipmunk, Striped hyena, Zebrafish, "
                                    "Thylacine, Tiger shark, Banded mongoose, Clownfish, Raccoon"),
    "spots": ("spots?|spotted", "Leopard, Cheetah, Jaguar, Dalmatian, Ladybug, Spotted hyena, Ocelot, Whale shark, "
                                "Snow leopard, Serval, Giraffe, Margay, Spotted eagle ray, Appaloosa"),
    "horns": ("horns?|horned", "Rhinoceros, Goat, Ram, Bull, Ibex, Oryx, Markhor, Bison, Buffalo, Gazelle, Antelope, "
                               "Kudu, Saiga, Jackson's chameleon, Horned lizard, Musk ox"),
    "wings": ("wings?|winged", "Eagle, Bat, Butterfly, Bee, Dragonfly, Owl, Parrot, Moth, Hummingbird, Albatross, "
                               "Flying fox, Pelican, Ladybug, Wasp"),
    "scales": ("scales?|scaly", "Snake, Lizard, Crocodile, Alligator, Pangolin, Iguana, Salmon, Gecko, Komodo dragon, "
                                "Carp, Monitor lizard, Gharial, Tuatara, Sturgeon"),
}

# Countries: name -> (region, landlocked)
_COUNTRIES = {
    "africa": ("Algeria, Angola, Benin, Botswana*, Burkina Faso*, Burundi*, Cape Verde, Cameroon, "
               "Central African Republic*, Chad*, Comoros, Democratic Republic of the Congo, Republic of the Congo, "
               "Djibouti, Egypt, Equatorial Guinea, Eritrea, Eswatini*, Ethiopia*, Gabon, Gambia, Ghana, Guinea, "
               "Guinea-Bissau, Ivory Coast, Kenya, Lesotho*, Liberia, Libya, Madagascar, Malawi*, Mali*, Mauritania, "
               "Mauritius, Morocco, Mozambique, Namibia, Niger*, Nigeria, Rwanda*, Sao Tome and Principe, Senegal, "
               "Seychelles, Sierra Leone, Somalia, South Africa, South Sudan*, Sudan, Tanzania, Togo, Tunisia, "
               "Uganda*, Zambia*, Zimbabwe*"),
    "europe": ("Albania, Andorra*, Austria*, Belarus*, Belgium, Bosnia and Herzegovina, Bulgaria, Croatia, Cyprus, "
               "Czech Republic*, Denmark, Estonia, Finland, France, Germany, Greece, Hungary*, Iceland, Ireland, "
               "Italy, Kosovo*, Latvia, Liechtenstein*, Lithuania, Luxembourg*, Malta, Moldova*, Monaco, Montenegro, "
               "Netherlands, North Macedonia*, Norway, Poland, Portugal, Romania, Russia, San Marino*, Serbia*, "
               "Slovakia*, Slovenia, Spain, Sweden, Switzerland*, Ukraine, United Kingdom, Vatican City*"),
    "asia": ("Afghanistan*, Armenia*, Azerbaijan*, Bahrain, Bangladesh, Bhutan*, Brunei, Cambodia, China, Georgia, "
             "India, Indonesia, Iran, Iraq, Israel, Japan, Jordan, Kazakhstan*, Kuwait, Kyrgyzstan*, Laos*, Lebanon, "
             "Malaysia, Maldives, Mongolia*, Myanmar, Nepal*, North Korea, Oman, Pakistan, Philippines, Qatar, "
             "Saudi Arabia, Singapore, South Korea, Sri Lanka, Syria, Taiwan, Tajikistan*, Thailand, East Timor, "
             "Turkey, Turkmenistan*, United Arab Emirates, Uzbekistan*, Vietnam, Yemen"),
    "north america": ("Antigua and Barbuda, Bahamas, Barbados, Belize, Canada, Costa Rica, Cuba, Dominica, "
                      "Dominican Republic, El Salvador, Grenada, Guatemala, Haiti, Honduras, Jamaica, Mexico, "
                      "Nicaragua, Panama, Saint Kitts and Nevis, Saint Lucia, Saint Vincent and the Grenadines, "
                      "Trinidad and Tobago, United States"),
    "south america": ("Argentina, Bolivia*, Brazil, Chile, Colombia, Ecuador, Guyana, Paraguay*, Peru, Suriname, "
                      "Uruguay, Venezuela"),
    "oceania": ("Australia, Fiji, Kiribati, Marshall Islands, Micronesia, Nauru, New Zealand, Palau, "
                "Papua New Guinea, Samoa, Solomon Islands, Tonga, Tuvalu, Vanuatu"),
}
COUNTRIES = {}
for _region, _text in _COUNTRIES.items():
    for _name in _items(_text):
        COUNTRIES[_name.rstrip("*")] = (_region, _name.endswith("*"))
# Other names the game may accept for a country (former names, official names, short forms).
COUNTRY_ALIASES = set(_items(
    "USA, US, United States of America, America, UK, Great Britain, Britain, England, Scotland, Wales, "
    "Northern Ireland, Holland, The Netherlands, Republic of Ireland, Czechia, Burma, Swaziland, Cabo Verde, "
    "Cote d'Ivoire, Côte d'Ivoire, Timor-Leste, DR Congo, DRC, Congo, Vatican, Holy See, Macedonia, Turkiye, "
    "Türkiye, Brunei Darussalam, Bahrein, Persia, Siam, Ceylon, Zaire, Palestine, The Gambia, The Bahamas, "
    "UAE, Emirates, South Korea, North Korea, Korea, St Lucia, St Kitts and Nevis, St Vincent and the Grenadines, "
    "Federated States of Micronesia, Kyrgyz Republic, Lao PDR, Russian Federation, Viet Nam"))
_KNOWN_COUNTRIES = None


def is_country(name):
    global _KNOWN_COUNTRIES
    if _KNOWN_COUNTRIES is None:
        _KNOWN_COUNTRIES = {_fold(n) for n in list(COUNTRIES) + list(COUNTRY_ALIASES)}
    return _fold(name) in _KNOWN_COUNTRIES or _fold(re.sub(r"^the\s+", "", name, flags=re.I)) in _KNOWN_COUNTRIES


# Countries most players name first; the rest count as rarer.
_COMMON_COUNTRIES = set(_items("United States, Canada, Mexico, Brazil, Argentina, United Kingdom, France, Germany, "
                               "Italy, Spain, Russia, China, Japan, India, Australia, Egypt, South Africa"))
_REGION_WORDS = {
    "europe": r"europe(?:an)?", "africa": r"africa(?:n)?", "asia": r"asia(?:n)?",
    "north america": r"north americ(?:a|an)|central americ(?:a|an)|caribbean",
    "south america": r"south americ(?:a|an)|latin americ(?:a|an)", "oceania": r"oceania(?:n)?|pacific island",
}

# --- prompt matching -------------------------------------------------------------

_LEAD = r"name (?:(?:a|an|any|one|some|the|another)\s+)?(?:(?:type|kind|sort|variety|species|breed)s?\s+of\s+)?"
_CONNECTORS = r"(?:\s+(?:in|from|with|that|which|who|is|are|has|have|a|an|the|its|their|whose))*"


def _fold(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _core(prompt, rules):
    """The prompt with rule wording and dangling connectors removed, folded."""
    core = _fold(prompt_rules.without_rules(prompt, rules))
    core = re.sub(r"\b(?:in|within) (?:its|their|the) (?:english )?name\b", " ", core)
    while True:
        trimmed = re.sub(r"\s+(?:that|which|who|whose(?: english)? name|whose|with|has|have|is|are|and|or|but|"
                         r"where|when|its|their|letters?|the|in|of)$", "", core).strip()
        if trimmed == core:
            return re.sub(r"\s+", " ", core)
        core = trimmed


def _is(core, nouns, extra=""):
    return re.fullmatch(_LEAD + rf"(?:{extra})?(?:{nouns}){_CONNECTORS}", core) is not None


def _strip(core, pattern):
    """Remove pattern from core; returns (core, matched text or None)."""
    m = re.search(rf"\b(?:{pattern})\b", core)
    if not m:
        return core, None
    return re.sub(r"\s+", " ", core[:m.start()] + " " + core[m.end():]).strip(), m.group(0)


def classify(prompt, rules=None):
    """What a prompt asks for, if we have built-in knowledge of it.

    Returns e.g. ("country", {"region": "europe"}), ("animal", {"trait": "stripes"}),
    ("word", {}), ("fruit", {}) or None.
    """
    rules = rules or prompt_rules.parse(prompt)
    core = _core(prompt, rules)
    if not core.startswith("name "):
        return None
    # English words, including compound words and syllable counts
    word_core, syllables = _strip(core, r"(?:\d|one|two|three|four|five|six|seven)[ -]syllables?")
    word_core = re.sub(r"\s+with$", "", word_core)
    word_core, compound = _strip(word_core, r"compound")
    if _is(word_core, r"(?:english |real |dictionary )?words?"):
        return "word", {"syllables": _syllables_in(syllables), "compound": bool(compound)}
    # Countries, optionally by region and coastline
    country_core, landlocked = _strip(core, r"landlocked|without (?:a )?coast(?:line)?|with no coast(?:line)?")
    country_core, coastal = _strip(country_core, r"coastal|with (?:a )?coast(?:line)?|that has (?:a )?coast(?:line)?|"
                                                 r"bordering (?:an|the) (?:sea|ocean)")
    region = None
    for name, pattern in _REGION_WORDS.items():
        country_core, found = _strip(country_core, pattern)
        if found:
            region = name
            break
    if _is(country_core, r"countr(?:y|ies)|nations?|sovereign states?"):
        return "country", {"region": region, "landlocked": bool(landlocked), "coastal": bool(coastal)}
    # Animals, optionally with a trait
    animal_core, trait = core, None
    for name, (pattern, _) in ANIMAL_TRAITS.items():
        animal_core, found = _strip(core, rf"(?:a |an )?(?:{pattern})")
        if found:
            trait = name
            break
    if _is(animal_core, r"animals?|creatures?"):
        return "animal", {"trait": trait}
    for name, (nouns, _) in CATEGORIES.items():
        if _is(core, nouns):
            return name, {}
    return None


def _syllables_in(text):
    if not text:
        return None
    n = text.split()[0].split("-")[0]
    numbers = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
    return int(n) if n.isdigit() else numbers.get(n)


# --- answers -------------------------------------------------------------------

def _entries(kind, details):
    """(answer, common?) pairs for a classified prompt."""
    if kind == "country":
        for name, (region, landlocked) in COUNTRIES.items():
            if details["region"] and region != details["region"]:
                continue
            if details["landlocked"] and not landlocked or details["coastal"] and landlocked:
                continue
            yield name, name in _COMMON_COUNTRIES
    elif kind == "animal":
        if details["trait"]:
            for i, name in enumerate(_items(ANIMAL_TRAITS[details["trait"]][1])):
                yield name, i < 4
        else:
            for group in ("common", "rare"):
                for name in _items(ANIMALS[group]):
                    yield name, group == "common"
    elif kind in CATEGORIES:
        for group in ("common", "rare"):
            for name in _items(CATEGORIES[kind][1][group]):
                yield name, group == "common"


_AFFIXES = {"dis", "mis", "non", "pre", "sub", "uni", "tri", "pro", "anti", "over", "under", "ing", "ness",
            "less", "ment", "able", "tion", "ers", "est", "ful"}


def _is_compound(word, dictionary):
    """Two real words joined (sunflower = sun + flower), not a prefix or suffix (disunity)."""
    for i in range(3, len(word) - 2):
        head, tail = word[:i], word[i:]
        if (head in dictionary and tail in dictionary and head not in _AFFIXES and tail not in _AFFIXES
                and is_real_word(head) is not False and is_real_word(tail) is not False):
            return True
    return False


def answers(prompt, limit=12):
    """Built-in answers for a prompt: [(answer, est_tier, confidence, source)], best first.

    Empty when we know nothing reliable about the prompt. Word answers need the word
    list; call load_words() first (it's fast after the first time).
    """
    rules = prompt_rules.parse(prompt)
    found = classify(prompt, rules)
    if not found:
        return []
    kind, details = found
    out = []
    if kind == "word":
        dictionary = words(wait=True)
        if not dictionary or not (rules.starts or rules.ends or rules.contains or rules.length
                                  or details["syllables"]):
            return []  # "Name a (compound) word" with no spelling rule: too broad to pick good ones
        matches = [w for w in dictionary
                   if 3 <= len(w) <= 16 and rules.violation(w) is None
                   and (not details["syllables"] or details["syllables"] in dictionary[w])
                   and (not details["compound"] or _is_compound(w, dictionary))]
        # Longer words tend to be rarer (more points). Sample evenly across lengths, longest
        # first, keeping only words the spell checker accepts (drops surnames and places).
        matches.sort(key=lambda w: (-len(w), zlib.crc32(w.encode())))  # fixed shuffle within a length
        step = max(1, len(matches) // 3000)
        checked = winspell.is_word("kitchen") is not None
        for w in matches[::step]:
            if checked and not winspell.is_word(w):
                continue
            tier = "deep_cut" if len(w) >= 10 else "rare" if len(w) >= 7 else "schooler"
            out.append((w, tier, 0.8 if checked else 0.5, "dictionary"))
            if len(out) >= limit * 4:
                break
        # Keep an even spread of lengths rather than only the longest words.
        return out[::max(1, len(out) // limit)][:limit]
    entries = [(name, common) for name, common in _entries(kind, details) if rules.violation(name) is None]
    rare = [n for n, c in entries if not c]
    common = [n for n, c in entries if c]
    for name in rare[:limit - min(len(common), limit // 3)]:
        out.append((name, "rare", 0.85, "local"))
    for name in common[:limit - len(out)]:
        out.append((name, "plankton", 0.95, "local"))
    return out


# --- checking model answers ----------------------------------------------------------

CHECKED_KINDS = ("word", "country")  # categories where we can tell a wrong answer


def check(prompt, answer, rules=None, found=False):
    """Extra checks for word and country prompts. Returns (violation, doubt), each a short phrase or None.

    violation: definitely breaks the prompt (the dictionary knows the word and its
    syllable count is wrong). doubt: probably wrong (not a real word, not a country we
    know), so the answer is ranked lower but still shown, since no list is perfect.
    Pass found (the result of classify) when checking many answers to one prompt.
    """
    rules = rules or prompt_rules.parse(prompt)
    if found is False:
        found = classify(prompt, rules)
    if not found or found[0] not in CHECKED_KINDS:
        return None, None
    if found[0] == "country":
        return None, None if is_country(answer) else "not a country I know"
    dictionary = words(wait=False) or {}
    word = re.sub(r"[^a-z]", "", _fold(answer))
    if " " not in answer.strip() and is_real_word(word, dictionary) is False:
        return None, "not in the dictionary"
    syllables = found[1]["syllables"]
    if syllables and word in dictionary and syllables not in dictionary[word]:
        counts = "/".join(str(c) for c in sorted(dictionary[word]))
        return f"has {counts} syllable{'s' if counts != '1' else ''}, not {syllables}", None
    if found[1]["compound"] and not _is_compound(word, dictionary):
        return None, "doesn't look like a compound word"
    return None, None
