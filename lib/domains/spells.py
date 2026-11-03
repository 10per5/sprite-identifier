"""Spell-icon catalogue domain.

Spell icons share the item pipeline (hashing, metadata, LLM passes, TOML
catalogue) but use a spell-centred vocabulary:

  category    = what the spell does (offensive, defensive, healing, ...)
  subcategory = element/school (fire, ice, arcane, nature, ...)
  tags        = element/function/target/effect/visual/color descriptors

Keeping this vocabulary here means the generic CLI and LLM loop never need to
know whether it is cataloguing swords or fireballs.
"""
import re

from lib.domains.base import Domain, PromptSet

SPELL_TAXONOMY = {
    "categories": [
        "offensive", "defensive", "healing", "buff", "debuff", "control",
        "summoning", "movement", "utility", "divination", "other",
    ],
    "subcategories": [
        "fire", "ice", "lightning", "earth", "air", "water", "poison",
        "holy", "shadow", "nature", "arcane", "physical",
    ],
    "element_tags": [
        "fire", "ice", "cold", "lightning", "earth", "air", "water", "poison",
        "acid", "arcane", "holy", "shadow", "nature", "physical",
    ],
    "function_tags": [
        "damage", "projectile", "beam", "area", "melee", "shield", "heal",
        "cleanse", "revive", "buff", "debuff", "curse", "stun", "slow", "root",
        "silence", "fear", "charm", "sleep", "blind", "weaken", "armor_break",
        "haste", "teleport", "blink", "flight", "invisibility", "stealth",
        "summon", "pet", "transform", "barrier", "trap", "dispel", "detect",
        "reveal", "scry",
    ],
    "target_tags": [
        "self", "ally", "enemy", "area", "single_target", "cone", "line",
        "ground", "aura", "touch",
    ],
    "effect_tags": [
        "explosion", "burst", "wave", "orb", "bolt", "missile", "beam",
        "slash", "spike", "chain", "vortex", "rain", "cloud", "shield", "rune",
        "sigil", "circle", "portal", "tentacle", "claw", "bite", "breath",
        "gaze",
    ],
    "visual_tags": [
        "glowing", "radiant", "dark", "fiery", "frosty", "electric",
        "ethereal", "spectral", "swirling", "sparkling", "smoky", "bloody",
        "fleshy", "mechanical", "natural", "crystalline", "holy", "demonic",
    ],
    "quality_tags": [
        "common", "uncommon", "rare", "legendary", "ancient", "forbidden",
        "divine", "demonic", "primal", "arcane",
    ],
    "school_tags": [
        "pyromancy", "cryomancy", "aeromancy", "geomancy", "hydromancy",
        "electromancy", "necromancy", "druidic", "holy", "shadow", "arcane",
        "illusion", "enchantment", "conjuration", "abjuration",
        "transmutation", "divination",
    ],
    "size_tags": ["small", "medium", "large", "huge"],
    "color_tags": [
        "red", "orange", "yellow", "green", "blue", "purple", "pink", "white",
        "black", "gray", "brown", "cyan", "magenta", "gold", "silver",
    ],
}


SYSTEM_PROMPT = """You are an expert at identifying pixel-art spell and ability icons from fantasy RPGs.

Return ONLY a JSON object with these keys:
  - name: short snake_case spell name (e.g. "fireball", "frost_nova", "healing_light")
  - category: one of: offensive, defensive, healing, buff, debuff, control, summoning, movement, utility, divination, other
  - subcategory: element or school (e.g. "fire", "ice", "lightning", "earth", "air", "water", "poison", "holy", "shadow", "nature", "arcane", "physical")
  - tags: array of short lowercase descriptor strings (element, function, target, effect, visual, quality, color)
  - confidence: float 0-1
  - notes: one short sentence

The icon may show a projectile, aura, summon, curse, shield, gesture, or impact.
Describe the magical effect, not a physical item. Do not include markdown fences.
Just raw JSON."""


USER_PROMPT_TEMPLATE = """Spell icon filename: {sprite_id}
Category hint: {category}
Name hint: {name}

Choose the best-fitting spell name, category, subcategory, and tags.
Return JSON with name, category, subcategory, tags, confidence, notes."""


TEXT_SYSTEM_PROMPT = """You are an expert at identifying pixel-art spell and ability icons from fantasy RPGs. You are NOT shown the image — infer the spell purely from its filename and the provided hints.

Return ONLY a JSON object with these keys:
  - name: short snake_case spell name (e.g. "fireball", "frost_nova", "healing_light")
  - category: one of: offensive, defensive, healing, buff, debuff, control, summoning, movement, utility, divination, other
  - subcategory: element or school (e.g. "fire", "ice", "lightning", "earth", "air", "water", "poison", "holy", "shadow", "nature", "arcane", "physical")
  - tags: array of short lowercase descriptor strings (element, function, target, effect, visual, quality, color)
  - confidence: float 0-1
  - notes: one short sentence

The filename is your only evidence, so read it closely: element words, effect
words, targets, colors, and gestures all matter. Describe a magical effect, not
a physical item. Do not include markdown fences. Just raw JSON.
Never return an empty or blank response: always output the JSON object. If you
are unsure about a field, use an empty string ("") or empty array ([]) rather
than omitting it."""


TEXT_USER_PROMPT_TEMPLATE = """Spell icon filename: {sprite_id}
Category hint: {category}
Name hint: {name}

Choose the best-fitting spell name, category, subcategory, and tags using ONLY
these hints (no image is provided).
Return JSON with name, category, subcategory, tags, confidence, notes.
Respond with the JSON object only — never leave the response blank or empty."""


SPELL_PROMPTS = PromptSet(
    vision_system=SYSTEM_PROMPT,
    vision_user=USER_PROMPT_TEMPLATE,
    text_system=TEXT_SYSTEM_PROMPT,
    text_user=TEXT_USER_PROMPT_TEMPLATE,
)


SPELL_CATEGORY_OPTIONS = tuple(SPELL_TAXONOMY["categories"])

# Abbreviations and near-synonyms seen in model output (`off`, `def`, `mov`
# ended up as catalogue folders before categories were canonicalised).
SPELL_CATEGORY_ALIASES = {
    "off": "offensive", "offense": "offensive", "attack": "offensive",
    "damage": "offensive",
    "def": "defensive", "defense": "defensive", "defence": "defensive",
    "protection": "defensive",
    "heal": "healing",
    "mov": "movement", "mobility": "movement",
    "summon": "summoning",
    "ctrl": "control", "crowd_control": "control",
    "util": "utility",
    "div": "divination", "detection": "divination",
}


# ---------------------------------------------------------------------------
# Filename/folder -> (category, subcategory) heuristics
# ---------------------------------------------------------------------------
# These only pre-fill hints. The LLM (vision for new icons, text for already
# named ones) remains the source of truth for the final metadata.
_FUNCTION_WORDS = {
    "healing": (
        "heal", "healing", "cure", "restore", "restoration", "revive",
        "revival", "regeneration", "regen", "life", "health", "mend",
        "recovery", "resurrection", "lifesteal", "purify",
    ),
    "defensive": (
        "shield", "barrier", "ward", "armor", "armour", "protect",
        "protection", "guard", "aegis", "sanctuary", "bulwark", "block",
        "parry", "wall", "fortify", "endure", "resilience", "absorb",
    ),
    "buff": (
        "buff", "empower", "enhance", "haste", "strength", "bless", "rage",
        "frenzy", "focus", "reinforce", "enlarge", "fury", "boost",
        "inspire", "might", "agility", "invigorate", "adrenaline",
    ),
    "debuff": (
        "debuff", "curse", "weaken", "slow", "silence", "blind", "fear",
        "sleep", "paralyze", "paralyzed", "petrify", "disease", "plague",
        "decay", "break", "shatter", "vulnerable", "frailty", "exhaustion",
        "misfortune", "doom", "hex", "jinx", "corruption", "rot", "bleed",
    ),
    "control": (
        "stun", "freeze", "root", "entangle", "trap", "bind", "immobilize",
        "confuse", "confusing", "charm", "knockback", "pull", "push", "daze",
        "mesmerize", "hypnotize", "snare",
    ),
    "summoning": (
        "summon", "conjure", "call", "raise dead", "raise skeleton",
        "raise undead", "raise zombie", "raise ally", "raise minion",
        "animate", "familiar", "minion", "demon", "undead", "skeleton",
        "spirit", "totem", "portal", "conjuration", "invoke", "spawn",
    ),
    "movement": (
        "teleport", "blink", "dash", "leap", "fly", "flight", "speed", "jump",
        "charge", "sprint", "shift", "warp", "retreat", "dodge", "roll",
        "glide",
    ),
    "divination": (
        "detect", "reveal", "scry", "vision", "sight", "identify", "insight",
        "foresee", "locate", "sense", "clairvoyance", "awareness", "scout",
    ),
    "offensive": (
        "attack", "strike", "blast", "bolt", "missile", "arrow", "sword",
        "spear", "axe", "punch", "kick", "claw", "bite", "beam", "wave",
        "meteor", "nova", "orb", "shard", "spike", "blade", "explosion",
        "burst", "fire", "flame", "ice", "frost", "lightning", "thunder",
        "poison", "acid", "damage", "shot", "projectile", "hit", "slash",
        "pierce", "crush", "ray", "storm", "rain", "hail", "quake",
    ),
    "utility": (
        "dispel", "cleanse", "light", "lock", "unlock", "open", "close",
        "repair", "create", "transform", "transmute", "polymorph",
        "invisibility", "stealth", "map", "time", "hourglass", "mirror",
        "reflect", "mimic", "lever", "switch", "key", "pick", "gather",
        "craft", "cook", "fish", "dig", "mine", "camp", "rest", "loot",
        "chest", "door", "stairs",
    ),
}

_ELEMENT_WORDS = {
    "fire": (
        "fire", "flame", "flames", "lava", "magma", "ember", "embers",
        "burn", "burning", "inferno", "pyro", "pyromancer", "blaze", "scorch",
        "ash", "volcano", "sun", "solar", "heat",
    ),
    "ice": (
        "ice", "frozen", "frost", "frosty", "cold", "snow", "cryo",
        "cryomancer", "blizzard", "glacier", "chilly", "freeze", "icicle",
    ),
    "lightning": (
        "lightning", "thunder", "electric", "electricity", "storm", "shock",
        "spark", "sparks",
    ),
    "earth": (
        "earth", "stone", "rock", "sand", "dust", "quake", "earthquake",
        "geo", "geomancy", "metal", "iron", "steel", "crystal",
    ),
    "air": (
        "air", "wind", "gale", "tornado", "cyclone", "aero", "aeromancer",
        "gust", "cloud",
    ),
    "water": (
        "water", "aqua", "hydro", "hydromancy", "hydrosophist", "tide",
        "wave", "ocean", "sea", "rain", "bubble", "flood", "drown",
    ),
    "poison": (
        "poison", "poisonous", "toxic", "acid", "venom", "plague", "rot",
        "ooze", "slime", "disease", "decay", "corrosion",
    ),
    "holy": (
        "holy", "divine", "light", "radiant", "radiance", "sun", "solar",
        "sacred", "bless", "blessed", "heaven", "angel", "paladin", "halo",
        "dawn",
    ),
    "shadow": (
        "shadow", "dark", "darkness", "void", "nightmare", "doom", "death",
        "necro", "necromancer", "blood", "bloody", "demon", "demonic",
        "devil", "infernal", "curse", "cursed", "bone", "undead", "soul",
        "spectral", "phantom", "moon",
    ),
    "nature": (
        "nature", "druid", "plant", "thorn", "vine", "tree", "leaf", "root",
        "beast", "animal", "bear", "wolf", "snake", "spider", "insect",
        "growth", "bloom", "forest", "wood", "seed", "mushroom", "fang",
        "fairy",
    ),
    "arcane": (
        "arcane", "magic", "magical", "spell", "mana", "rune", "sigil",
        "portal", "mystic", "wizard", "warlock", "sorcery", "enchant",
        "enchantment", "illusion", "mirror", "time", "hourglass", "star",
        "cosmic", "astral",
    ),
    "physical": (
        "physical", "slash", "strike", "punch", "kick", "blade", "arrow",
        "spear", "sword", "axe", "hammer", "mace", "claw", "bite", "fang",
        "fist", "weapon", "melee", "knight", "swordsman", "barbarian",
        "archer", "crossbowman", "blacksmith", "crafter", "pirate", "thief",
    ),
}

# Explicit folder-level function hints (e.g. in/spellicon-named/debuff/).
_FOLDER_FUNCTION = {
    "debuff": "debuff",
    "buff": "buff",
    "healing": "healing",
    "defensive": "defensive",
    "summoning": "summoning",
    "movement": "movement",
    "divination": "divination",
    "utility": "utility",
}

# Folder-level element/school hints for class-themed icon packs.
_FOLDER_ELEMENT = {
    "fire": "fire",
    "pyromancer": "fire",
    "ice icons": "ice",
    "cryomancer": "ice",
    "aeromancer": "air",
    "earthmage": "earth",
    "geomancer": "earth",
    "druid": "nature",
    "fairy": "nature",
    "paladin": "holy",
    "necromancer": "shadow",
    "warlock": "shadow",
    "demon": "shadow",
    "halloween": "shadow",
    "knight": "physical",
    "swordsman": "physical",
    "barbarian": "physical",
    "archer": "physical",
    "crossbowman": "physical",
    "blacksmith": "physical",
    "crafter": "physical",
    "pirate": "physical",
    "thief": "physical",
}


def _normalise(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _matches(text, words):
    """Token-aware keyword match.

    Short words (<=3 chars, e.g. "air", "ice") must match a whole token so
    "fairy" does not become "air". Longer words may match inside compounds
    ("firehead", "flamesword").
    """
    norm = _normalise(text)
    tokens = set(norm.split())
    for word in words:
        phrase = word.replace("_", " ").replace("-", " ")
        if " " in phrase:
            if f" {phrase} " in f" {norm} ":
                return True
        elif len(phrase) <= 3:
            if phrase in tokens:
                return True
        elif phrase in norm:
            return True
    return False


def _first_match(text, mapping):
    for label, words in mapping.items():
        if _matches(text, words):
            return label
    return None


def classify(relative_path):
    """Return ``(category, subcategory)`` hints for a spell icon path."""
    normalised = relative_path.replace("\\", "/")
    parts = normalised.split("/")
    name = parts[-1].rsplit(".", 1)[0]
    folders = parts[:-1]

    # 1) Explicit function folder wins (all icons in named/debuff are debuffs,
    #    even if their filenames mention flames etc.).
    for folder in folders:
        key = folder.lower()
        for marker, category in _FOLDER_FUNCTION.items():
            if marker in key:
                category_hint = category
                break
        else:
            continue
        break
    else:
        category_hint = _first_match(name, _FUNCTION_WORDS)

    subcategory = _first_match(name, _ELEMENT_WORDS)
    if not subcategory:
        for folder in folders:
            key = folder.lower()
            for marker, element in _FOLDER_ELEMENT.items():
                if marker in key:
                    subcategory = element
                    break
            if subcategory:
                break

    # Spell icons are usually active effects; if only an element is known, treat
    # it as an offensive spell. Otherwise fall back to utility.
    if not category_hint:
        category_hint = "offensive" if subcategory else "utility"
    return category_hint, subcategory or ""


SPELL_DOMAIN = Domain(
    key="spells",
    title="Spells",
    source_dir="in/spellicon",
    index_dir="in/spellicon",
    metadata="out/spells/metadata.json",
    catalog="out/@spells",
    # Raw packs in in/spellicon go through review-approval + move-approved
    # (-> out/spells/approved) like items; only the curated named pack builds
    # directly. `build` still gates every entry.
    build_sources=("out/spells/approved", "in/spellicon-named"),
    labels_json="out/spells/labels.json",
    label_review_html="out/spells/label_review.html",
    approval_html="out/spells/approve_reject.html",
    approval_json="out/spells/approve_reject.json",
    approved_dir="out/spells/approved",
    catalog_view_html="out/spells/catalog_view.html",
    categories=SPELL_CATEGORY_OPTIONS,
    taxonomy=SPELL_TAXONOMY,
    prompts=SPELL_PROMPTS,
    classify=classify,
    category_aliases=SPELL_CATEGORY_ALIASES,
    recursive=True,
    extensions=(".png", ".jpg", ".jpeg"),
    default_source="spellicon",
    reference_sources=(),
    # After the AI passes, review-label shows only the icons that are still
    # blocked (placeholder names, no tags) for manual naming/tagging.
    wizard_steps=(
        "index", "dedup", "label", "infer", "review-label", "apply-labels",
        "review-approval", "move-approved", "build", "viewer",
    ),
    named_source_dir="in/spellicon-named",
)
