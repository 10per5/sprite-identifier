"""Readiness rules shared by every pipeline stage.

`label` / `infer` use :func:`apply_llm_result` so an empty or partial model
response never masquerades as a finished entry; `build` and `revert` use
:func:`entry_issues` to decide whether an entry may live in the catalogue.

Reason codes returned by :func:`entry_issues`:

  placeholder_name    empty, ``unnamed``, ``icon12``, ``sprite_0031``, digits only,
                      or only pack words + counters (``ability_icons1_12``)
  invalid_category    not in the domain vocabulary (after alias expansion)
  missing_tags        no tags at all
  unprocessed         never got a usable LLM response and no human touched it
  flagged_for_review  the last LLM response was incomplete
  rejected            a human rejected it on the approve/reject page
  low_confidence      model confidence below the requested threshold

A human approval (``approved: true``, written by `move-approved`) waives the
soft checks (tags, unprocessed, review flag, confidence) but never the
structural ones (name, category), because those decide the catalogue path.
"""
import re

from lib.util.paths import sanitize_name

# Give up on an icon after this many incomplete LLM responses; it then waits
# for a human on the approve/reject page instead of burning more requests.
MAX_LLM_ATTEMPTS = 3

_PLACEHOLDER_RE = re.compile(
    r"^(unnamed(_\d+)?|unknown(_\w+)?|sprite_?\d+|icon_?\d+|image_?\d+|img_?\d+|\d+)$"
)
# Pack-filename words that carry no meaning on their own. A name made only of
# these plus counters (``ability_icons1_12``, ``resized_passive2``,
# ``skill_icon_03``) is a filename, not a label.
_GENERIC_WORDS = {
    "icon", "icons", "ability", "abilities", "skill", "skills", "spell",
    "spells", "sprite", "sprites", "image", "img", "pic", "tile", "asset",
    "passive", "active", "resized", "new", "copy", "final", "unnamed", "untitled",
    "frame", "sheet", "pixel", "art", "rpg", "free",
}

HARD_ISSUES = ("placeholder_name", "invalid_category")
SOFT_ISSUES = ("missing_tags", "unprocessed", "flagged_for_review", "low_confidence")


def category_vocabulary(domain):
    """Top-level category names accepted for ``domain``.

    Spells use flat categories; items store ``weapons`` or ``weapons/swords``,
    so the first path segment is what gets validated.
    """
    vocab = {c.split("/", 1)[0] for c in domain.categories}
    for key in ("categories", "top_categories"):
        vocab.update(domain.taxonomy.get(key) or ())
    return vocab


def canonical_category(value, domain):
    """Normalise a category to the domain vocabulary, or ``None`` if junk.

    Accepts exact matches, ``domain.category_aliases`` (``off`` → ``offensive``)
    and unambiguous prefixes of at least three letters (``summon`` →
    ``summoning``). Item sub-paths keep their tail (``Weapons/Swords`` →
    ``weapons/swords``).
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace("\\", "/")
    if not text:
        return None
    head, sep, tail = text.partition("/")
    head = re.sub(r"[^a-z0-9]+", "_", head).strip("_")
    vocab = category_vocabulary(domain)
    if head not in vocab:
        head = domain.category_aliases.get(head, head)
    if head not in vocab:
        matches = [c for c in vocab if len(head) >= 3 and c.startswith(head)]
        if len(matches) != 1:
            return None
        head = matches[0]
    return f"{head}{sep}{tail}" if tail else head


def is_placeholder_name(name):
    """True for names that carry no meaning (filename counters, blanks)."""
    if not isinstance(name, str) or not name.strip():
        return True
    slug = sanitize_name(name)
    if _PLACEHOLDER_RE.match(slug):
        return True
    words = [re.sub(r"\d+$", "", w) for w in slug.split("_")]
    words = [w for w in words if w]
    return all(w in _GENERIC_WORDS for w in words)


def clean_tags(tags):
    """Lower-case, de-duplicated, non-empty string tags (order preserved)."""
    if not isinstance(tags, list):
        return []
    seen = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        tag = re.sub(r"\s+", "_", tag.strip().lower())
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def _has_llm_evidence(entry):
    return bool(entry.get("vllm_tags")) or entry.get("llm_status") == "ok"


def entry_issues(entry, domain, *, min_confidence=None, variant=False):
    """Return the reason codes that keep ``entry`` out of the catalogue.

    Recolor variants (``variant=True``) are never sent to the LLM, so only the
    structural checks apply to them.
    """
    issues = []
    if is_placeholder_name(entry.get("name")):
        issues.append("placeholder_name")
    if canonical_category(entry.get("category"), domain) != entry.get("category"):
        issues.append("invalid_category")
    if variant:
        return issues

    if entry.get("approved") is False:
        issues.append("rejected")
        return issues
    if entry.get("approved"):
        return issues

    if not entry.get("tags"):
        issues.append("missing_tags")
    if not _has_llm_evidence(entry) and not entry.get("manual"):
        issues.append("unprocessed")
    if entry.get("needs_review"):
        issues.append("flagged_for_review")
    if min_confidence is not None:
        confidence = entry.get("confidence")
        if confidence is None:
            confidence = entry.get("vllm_confidence")
        if confidence is None or confidence < min_confidence:
            issues.append("low_confidence")
    return issues


def llm_retry_allowed(entry):
    """False once an entry has used up its incomplete-response budget."""
    return int(entry.get("llm_attempts") or 0) < MAX_LLM_ATTEMPTS


def apply_llm_result(entry, result, domain, *, take_name=True):
    """Merge a model response into ``entry`` only if it is actually useful.

    A response is *complete* when it yields a real name (if ``take_name``), a
    category from the vocabulary, and at least one tag. Complete responses
    overwrite the label fields; incomplete ones are recorded under
    ``vllm_last`` and leave the entry pending (``needs_review`` +
    ``llm_status="incomplete"``) so it is retried and never catalogued as-is.
    Human edits (``manual``) keep their name and category either way.

    Returns ``True`` when the response was complete.
    """
    name = sanitize_name(result.get("name") or "")
    category = canonical_category(result.get("category"), domain)
    tags = clean_tags(result.get("tags"))
    subcategory = (result.get("subcategory") or "").strip().lower()
    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        confidence = None

    missing = []
    if take_name and is_placeholder_name(name):
        missing.append("name")
    if category is None:
        missing.append("category")
    if not tags:
        missing.append("tags")

    entry["llm_attempts"] = int(entry.get("llm_attempts") or 0) + 1
    if missing:
        entry["vllm_last"] = {
            "name": result.get("name"),
            "category": result.get("category"),
            "subcategory": result.get("subcategory"),
            "tags": result.get("tags"),
            "confidence": confidence,
            "notes": result.get("notes", ""),
            "missing": missing,
        }
        entry["llm_status"] = "incomplete"
        entry["needs_review"] = True
        return False

    manual = bool(entry.get("manual"))
    if take_name and not manual:
        entry["name"] = name
    if not manual or canonical_category(entry.get("category"), domain) is None:
        entry["category"] = category
    if subcategory or not entry.get("subcategory"):
        entry["subcategory"] = subcategory
    if not manual or not entry.get("tags"):
        entry["tags"] = tags
    entry["confidence"] = confidence

    entry["vllm_name"] = name if take_name else entry.get("name")
    entry["vllm_category"] = category
    entry["vllm_tags"] = tags
    entry["vllm_confidence"] = confidence
    entry["vllm_notes"] = result.get("notes", "")
    entry["llm_status"] = "ok"
    entry["llm_attempts"] = 0
    entry.pop("needs_review", None)
    entry.pop("vllm_last", None)
    entry["manual"] = manual
    return True
