"""Sprite categorization for DCSS-style item sprites.

Top-level groups (user-requested):
  weapons -> swords, daggers, clubs (incl. morning stars), lances (polearms,
            etc), ranged (xbow, bow, arrow, bolt, spear, shuriken, throwables),
            axes, hammers, flails, whips, scythes, staves, other
  armor   -> head, chest, legs, boots, gloves, shields
  food    -> food, food/fruit, food/fish, food/meat, food/vegetables
  consumables -> potions, scrolls, books
  magic   -> wands
  treasure -> gems, jewelry, currency, keys, runes
  tools   -> tool, tableware, storage
  accessories
  environment -> floor, walls
  effects -> effect, blood
  other   -> everything else

Returns a category string such as "weapons/swords" or "armor/chest".
"""
from pathlib import Path

WEAPONS = "weapons"
ARMOR = "armor"
FOOD = "food"
CONSUMABLES = "consumables"
MAGIC = "magic"
TREASURE = "treasure"
TOOLS = "tools"
ACCESSORIES = "accessories"
ENVIRONMENT = "environment"
EFFECTS = "effects"
OTHER = "other"

_WEAPON_TOPS = {
    "weapon", "weapons",
    "sword", "swords",
    "club", "clubs",
    "polearm", "polearms", "lance", "lances",
    "ranged", "firearm", "firearms", "explosive",
    "axe", "axes",
    "hammer", "hammers",
    "flail", "flails",
    "whip", "whips",
    "scythe", "scythes",
    "staff", "stave", "staves",
    "dagger", "daggers",
}

_WEAPON_SUB = {
    "swords": (
        "sword", "blade", "katana", "tsurugi", "falchion", "sabre", "scimitar",
        "cutlass", "longsword", "shortsword", "broadsword", "claymore", "rapier",
        "estoc", "zweihander", "greatsword", "great_sword",
    ),
    "daggers": ("dagger", "knife", "stiletto", "poniard", "vampire_tooth"),
    "clubs": (
        "club", "mace", "ankus", "morningstar", "morning_star", "eveningstar",
        "flanged_mace",
    ),
    "lances": (
        "polearm", "polearms", "lance", "lances", "glaive", "halberd",
        "bardiche", "trident", "trishula", "pike",
    ),
    "ranged": (
        "bow", "crossbow", "xbow", "arrow", "bolt", "spear", "javelin",
        "shuriken", "throw", "throwing", "thrown", "sling", "blowgun",
        "tomahawk", "rock", "stone", "boomerang", "dart", "needle",
    ),
    "axes": ("axe", "axes"),
    "hammers": ("hammer", "hammers"),
    "flails": ("flail", "flails", "scourge"),
    "whips": ("whip", "whips"),
    "scythes": ("scythe", "scythes"),
    "staves": (
        "staff", "stave", "staves", "lajatang", "sceptre", "wucad_mu",
    ),
}

_ARMOR_SUB = {
    "head": ("helmet", "headgear", "helm", "hat", "crown", "hood", "circlet", "mask"),
    "chest": ("armour", "armor", "torso", "robe", "chainmail", "plate", "scale", "leather", "mail"),
    "legs": ("leggings", "legging", "greave", "trousers", "kilt", "skirt"),
    "boots": ("boots", "footgear", "boot", "shoe", "sandal", "sabaton"),
    "gloves": ("glove", "gloves", "gauntlet", "mitt"),
    "shields": ("shield", "shields"),
}

_FOOD_SUB = {
    "fruit": ("fruit",),
    "fish": ("fish",),
    "meat": ("meat",),
    "vegetable": ("vegetable", "vegetables"),
    "vegetables": ("vegetable", "vegetables"),
}

_CONSUMABLES = {
    "potion": "consumables/potions",
    "scroll": "consumables/scrolls",
    "book": "consumables/books",
}

_TREASURE = {
    "gem": "treasure/gems",
    "mineral": "treasure/gems",
    "ingot": "treasure/gems",
    "amulet": "treasure/jewelry",
    "ring": "treasure/jewelry",
    "jewelry": "treasure/jewelry",
    "currency": "treasure/currency",
    "key": "treasure/keys",
    "rune": "treasure/runes",
}

_TOOLS = ("tool", "tools", "tableware", "storage")

_ENVIRONMENT = {
    "floor": "environment/floor",
    "wall": "environment/walls",
    "walls": "environment/walls",
}

_EFFECTS = ("effect", "effects", "blood")


def _contains_any(name, words):
    return any(w in name for w in words)


def _weapon_subcategory(name):
    for sub, words in _WEAPON_SUB.items():
        if _contains_any(name, words):
            return sub
    return None


def _armor_subcategory(top, name):
    if top in _ARMOR_SUB:
        for sub, words in _ARMOR_SUB.items():
            if top in words or _contains_any(name, words):
                return sub
    # Generic armour/torso/robe buckets inspected by filename.
    if top in {"armour", "armor", "torso", "robe"}:
        if _contains_any(name, ("helm", "helmet", "hat", "crown", "hood", "circlet", "mask")):
            return "head"
        if _contains_any(name, ("boot", "shoe", "sandal", "sabaton")):
            return "boots"
        if _contains_any(name, ("glove", "gauntlet", "mitt")):
            return "gloves"
        if _contains_any(name, ("legging", "greave", "trousers", "kilt", "skirt")):
            return "legs"
        return "chest"
    if top == "shield":
        return "shields"
    if top in {"helmet", "headgear"}:
        return "head"
    if top in {"boots", "footgear"}:
        return "boots"
    if top == "leggings":
        return "legs"
    if top == "glove":
        return "gloves"
    return None


def category_for(relative_path):
    """Categorize a DCSS-relative path like 'sword/long_sword.png'."""
    parts = [p.lower().replace(" ", "_") for p in Path(relative_path).parts]
    top = parts[0] if parts else "other"
    name = Path(relative_path).stem.lower().replace(" ", "_")

    # --- non-weapon top directories take priority (avoid filename false
    #     positives like floor/stone.png matching the "stone" throwable) ---
    if top in _CONSUMABLES:
        return _CONSUMABLES[top]
    if top == "wand":
        return f"{MAGIC}/wands"
    if top in _TREASURE:
        return _TREASURE[top]
    if top in _TOOLS:
        return TOOLS
    if top == "accessory":
        return ACCESSORIES
    if top in _ENVIRONMENT:
        return _ENVIRONMENT[top]
    if top in _EFFECTS:
        return EFFECTS

    # --- food ---
    if top in {"food", "fruit", "fish", "meat", "vegetable", "vegetables"}:
        nested = next((p for p in parts[1:] if p in _FOOD_SUB), "")
        if nested:
            return f"{FOOD}/{nested}"
        if top in _FOOD_SUB:
            return f"{FOOD}/{top}"
        return FOOD

    # --- armor ---
    sub = _armor_subcategory(top, name)
    if sub:
        return f"{ARMOR}/{sub}"

    # --- weapons (top dir or filename match) ---
    if top in _WEAPON_TOPS or _weapon_subcategory(name):
        sub = _weapon_subcategory(name)
        if sub:
            return f"{WEAPONS}/{sub}"
        # top dir hints
        if top in {"sword", "swords"}:
            return f"{WEAPONS}/swords"
        if top in {"club", "clubs"}:
            return f"{WEAPONS}/clubs"
        if top in {"polearm", "polearms", "lance", "lances"}:
            return f"{WEAPONS}/lances"
        if top in {"ranged", "firearm", "firearms", "explosive"}:
            return f"{WEAPONS}/ranged"
        if top in {"axe", "axes"}:
            return f"{WEAPONS}/axes"
        if top in {"hammer", "hammers"}:
            return f"{WEAPONS}/hammers"
        if top in {"flail", "flails"}:
            return f"{WEAPONS}/flails"
        if top in {"whip", "whips"}:
            return f"{WEAPONS}/whips"
        if top in {"scythe", "scythes"}:
            return f"{WEAPONS}/scythes"
        if top in {"staff", "stave", "staves"}:
            return f"{WEAPONS}/staves"
        if top in {"dagger", "daggers"}:
            return f"{WEAPONS}/daggers"
        return f"{WEAPONS}/other"

    return f"{OTHER}/{top or 'unclassified'}"
