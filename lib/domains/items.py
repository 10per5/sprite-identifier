"""Item catalogue domain (weapons, armor, treasure, ...).

This is the original DCSS/roguelike item taxonomy, now expressed as a
:class:`~lib.domains.base.Domain` profile so the shared pipeline can also run
spell icons without item-specific assumptions leaking into the generic code.
"""
from lib.core.sprite_categories import category_for
from lib.domains.base import Domain, PromptSet

TAG_TAXONOMY = {
    # Top-level categories
    "top_categories": [
        "weapons", "armor", "food", "consumables", "magic", "treasure",
        "tools", "accessories", "environment", "effects", "currency", "other"
    ],
    # Weapon subcategories (exhaustive, from dcss naming)
    "weapon_types": [
        "sword", "shortsword", "longsword", "broadsword", "claymore", "scimitar",
        "sabre", "falchion", "rapier", "estoc", "zweihander", "katana", "wakizashi",
        "tachi", "cutlass", "dagger", "knife", "katar", "tiger_claw", "knuckle_duster",
        "axe", "battle_axe", "war_axe", "double_axe", "throwing_axe", "bone_axe",
        "mace", "morning_star", "flanged_mace", "war_hammer", "maul", "giant_mallet",
        "flail", "scourge", "chain_flail", "dire_flail", "dragon_bone_flail",
        "spear", "javelin", "pike", "lance", "trident", "demon_trident", "gold_spear",
        "ice_spear", "orcish_spear", "elven_spear",
        "bow", "short_bow", "long_bow", "composite_bow", "arbalest", "hand_crossbow",
        "crossbow", "bolt", "arrow", "sling", "sling_stone",
        "shuriken", "dart", "needle", "boomerang", "tomahawk",
        "staff", "stave", "quarterstaff", "lajatang", "sceptre", "wucad_mu",
        "whip", "bullwhip", "chain_whip", "demon_whip", "fire_whip", "harpy_whip",
        "holy_scourge", "lightning_whip", "scythe", "war_scythe",
        "polearm", "glaive", "halberd", "bardiche", "voulge", "glaive_of_prune",
        "claw", "katar", "ankus", "trident",
        "other_weapon",
    ],
    # Armor subcategories
    "armor_types": [
        "helmet", "great_helm", "bascinet", "sallet", "chain_coif", "leather_helm",
        "hood", "mask", "crown", "circlet", "chain_mail", "plate_mail", "scale_mail",
        "splint_mail", "banded_mail", "leather_armor", "studded_leather", "hide_armor",
        "animal_skin", "robe", "cloak", "cape", "mantle", "gauntlets", "bracers",
        "vambraces", "shield", "buckler", "kite_shield", "tower_shield", "round_shield",
        "greaves", "leg_armor", "leggings", "boots", "sabaton", "shoes",
        "torso", "chain_mail_shirt", "plate_armor",
    ],
    # Food types
    "food_types": [
        "fruit", "vegetable", "meat", "fish", "bread", "ration", "cake", "cheese",
        "egg", "nut", "berry", "mushroom", "soup", "stew", "apple", "banana", "orange",
        "pear", "plum", "cherry", "grape", "melon", "carrot", "potato", "onion",
        "lettuce", "cabbage", "pepper", "tomato", "corn", "rice", "wheat", "oat",
        "bean", "pea", "mushroom", "fungus", "slime_mold", "ration", "candy",
        "chocolate", "pie", "cookie", "donut", "muffin", "pastry",
    ],
    # Consumables
    "consumable_types": [
        "potion", "scroll", "book", "wand", "ring", "amulet",
    ],
    # Treasure
    "treasure_types": [
        "gem", "ring", "amulet", "coin", "currency", "key", "rune",
        "ingot", "mineral", "jewelry", "artifact", "relic",
    ],
    # Tools
    "tool_types": [
        "ladder", "shovel", "pickaxe", "hammer", "saw", "knife", "tableware",
        "storage", "bag", "bucket", "compass", "lighter", "matches", "pack",
        "hoe", "jackhammer", "ankus", "fishing_rod", "fishing_net", "fish_trap",
    ],
    # Accessories
    "accessory_types": [
        "cloak", "ring", "amulet", "belt", "glove", "bag", "fishing_rod",
        "fishing_net", "fish_trap", "cork_bobber", "crab_trap", "fly_lure",
    ],
    # Environment
    "environment_types": [
        "floor", "wall", "door", "stairs", "rubble", "statue", "sign", "torch",
        "altar", "fountain", "grave", "chest", "crate", "barrel",
    ],
    # Effects
    "effect_types": [
        "blood", "fire", "ice", "lightning", "poison", "magic_effect", "cloud",
        "explosion", "sparkle", "aura",
    ],
    # Material tags
    "material_tags": [
        "steel", "iron", "bronze", "silver", "gold", "mithril", "adamantite",
        "wood", "leather", "bone", "stone", "cloth", "glass", "crystal",
        "obsidian", "pearl", "ivory", "copper", "brass", "lead", "plastic",
        "crystal", "dragon_bone",
    ],
    # Size descriptors
    "size_tags": [
        "small", "medium", "large", "huge", "tiny",
    ],
    # Combat descriptors
    "combat_tags": [
        "melee", "ranged", "thrown", "one_handed", "two_handed",
        "light", "heavy", "fast", "slow",
    ],
    # Quality/rarity
    "quality_tags": [
        "common", "uncommon", "rare", "magical", "cursed", "blessed",
        "ancient", "elven", "orcish", "dwarven", "giant", "demonic", "holy",
        "rusty", "ornate",
    ],
    # Visual/magical appearance
    "visual_tags": [
        "glowing", "magical", "enchanted", "cursed_item", "blessed_item",
        "ancient", "ornate", "simple", "rusty", "bloodstained", "frosty",
        "fiery", "sparkling", "dim", "bright", "translucent",
    ],
    # Color tags
    "color_tags": [
        "red", "green", "blue", "yellow", "orange", "gray", "black", "white",
        "cyan", "magenta", "dark", "light", "pink", "brown", "golden", "silver",
    ],
}


SYSTEM_PROMPT = """You are an expert at identifying pixel-art game items (DCSS-style roguelike).

Return ONLY a JSON object with these keys:
  - name: short descriptive name (e.g. "long_sword", "healing_potion", "chain_mail")
  - category: one of: weapons, armor, food, consumables, magic, treasure, tools, accessories, environment, effects, currency, other
  - subcategory: specific type (e.g. "longsword", "healing_potion", "chain_mail")
  - tags: array of short lowercase descriptor strings (material, size, combat, quality, visual, color tags)
  - confidence: float 0-1
  - notes: one short sentence

Do not include markdown fences. Just raw JSON."""


USER_PROMPT_TEMPLATE = """Sprite filename: {sprite_id}.png
Category hint: {category}
Name hint: {name}

Choose the best-fitting name, category, subcategory, and tags.
Return JSON with name, category, subcategory, tags, confidence, notes."""


# Text-only prompts (no sprite image is sent). The model has no visual evidence,
# so these lean on the filename + hints and are stricter about never returning an
# empty/blank response.
TEXT_SYSTEM_PROMPT = """You are an expert at identifying pixel-art game items (DCSS-style roguelike). You are NOT shown the image — infer the item purely from its filename and the provided hints.

Return ONLY a JSON object with these keys:
  - name: short descriptive name (e.g. "long_sword", "healing_potion", "chain_mail")
  - category: one of: weapons, armor, food, consumables, magic, treasure, tools, accessories, environment, effects, currency, other
  - subcategory: specific type (e.g. "longsword", "healing_potion", "chain_mail")
  - tags: array of short lowercase descriptor strings (material, size, combat, quality, visual, color tags)
  - confidence: float 0-1
  - notes: one short sentence

The filename and hints are your only evidence, so use them: read the descriptive
words (materials, shapes, qualities) to pick fitting tags and categories.
Do not include markdown fences. Just raw JSON.
Never return an empty or blank response: always output the JSON object. If you
are unsure about a field, use an empty string ("") or empty array ([]) rather
than omitting it."""


TEXT_USER_PROMPT_TEMPLATE = """Sprite filename: {sprite_id}.png
Category hint: {category}
Name hint: {name}

Choose the best-fitting name, category, subcategory, and tags using ONLY these
hints (no image is provided).
Return JSON with name, category, subcategory, tags, confidence, notes.
Respond with the JSON object only — never leave the response blank or empty."""


ITEM_PROMPTS = PromptSet(
    vision_system=SYSTEM_PROMPT,
    vision_user=USER_PROMPT_TEMPLATE,
    text_system=TEXT_SYSTEM_PROMPT,
    text_user=TEXT_USER_PROMPT_TEMPLATE,
)


# Category choices shown by the manual review pages. Keep this in sync with the
# folders emitted by `build` (it is the item vocabulary, not the spell one).
ITEM_CATEGORY_OPTIONS = (
    "weapons/swords", "weapons/daggers", "weapons/clubs", "weapons/lances",
    "weapons/ranged", "weapons/axes", "weapons/hammers", "weapons/flails",
    "weapons/whips", "weapons/scythes", "weapons/staves", "weapons/other",
    "armor/head", "armor/chest", "armor/legs", "armor/boots", "armor/gloves",
    "armor/shields", "food", "food/fruit", "food/fish", "food/meat",
    "food/vegetables", "consumables/potions", "consumables/scrolls",
    "consumables/books", "magic/wands", "treasure/gems", "treasure/jewelry",
    "treasure/currency", "treasure/keys", "treasure/runes", "tools",
    "accessories", "environment/floor", "environment/walls", "effects",
    "other/unclassified",
)


def classify(relative_path):
    """Items store their full sub-path in ``category``; no separate subcategory."""
    return category_for(relative_path), ""


ITEM_DOMAIN = Domain(
    key="items",
    title="Items",
    source_dir="in/sprites",
    index_dir="dcss",
    metadata="out/items/metadata.json",
    catalog="out/@items",
    build_sources=("in/sprites",),
    labels_json="out/items/labels.json",
    label_review_html="out/items/label_review.html",
    approval_html="out/items/approve_reject.html",
    approval_json="out/items/approve_reject.json",
    approved_dir="out/items/approved",
    catalog_view_html="out/items/catalog_view.html",
    categories=ITEM_CATEGORY_OPTIONS,
    taxonomy=TAG_TAXONOMY,
    prompts=ITEM_PROMPTS,
    classify=classify,
    recursive=False,
    extensions=(".png",),
    default_source="sprite",
    reference_sources=("dcss", "dlss"),
)
