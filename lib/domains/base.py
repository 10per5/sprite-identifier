"""Domain profile shared by every asset family (items, spells, ...).

The pipeline mechanics (hashing, metadata storage, LLM retries/progress,
cataloguing) are domain-agnostic. A :class:`Domain` bundles only the things that
actually differ between families:

  * where assets live and where their catalogue/metadata is written
  * the category vocabulary + tag taxonomy
  * the prompts sent to the LLM
  * a small ``classify`` function used to pre-fill name/category hints

Keeping these in one immutable object means a new family (e.g. spells) does not
require forking the CLI or the LLM loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# ``classify(relative_path) -> (category, subcategory)``. Category is the
# folder-level bucket used by the catalogue; subcategory is free-form metadata
# (e.g. a weapon type for items, an element/school for spells).
Classifier = Callable[[str], tuple[str, str]]


@dataclass(frozen=True)
class PromptSet:
    """The four prompt templates used by the vision and text LLM passes."""

    vision_system: str
    vision_user: str
    text_system: str
    text_user: str


@dataclass(frozen=True)
class Domain:
    """Everything that differs between asset families."""

    key: str
    title: str

    # I/O defaults -----------------------------------------------------------
    source_dir: str
    index_dir: str
    metadata: str
    catalog: str
    build_sources: tuple[str, ...]
    labels_json: str
    label_review_html: str
    approval_html: str
    approval_json: str
    approved_dir: str
    catalog_view_html: str

    # Domain vocabulary ------------------------------------------------------
    categories: tuple[str, ...]
    taxonomy: dict
    prompts: PromptSet
    classify: Classifier
    # Short/variant spellings the LLM (or a human) uses for a category, e.g.
    # ``{"off": "offensive"}``. Applied by ``lib.core.quality``.
    category_aliases: dict[str, str] = field(default_factory=dict)
    # Category-only prompts used by `recategorize` to re-judge catalogued
    # assets. ``None`` means the domain does not support that pass.
    recategorize_prompts: PromptSet | None = None

    # Scan/source behaviour --------------------------------------------------
    recursive: bool = False
    extensions: tuple[str, ...] = (".png",)
    default_source: str = "sprite"
    reference_sources: tuple[str, ...] = ()

    # Guided wizard ----------------------------------------------------------
    # The ordered steps this domain should show. Keys map to steps in
    # ``lib/wizard.py``; domains can omit steps that don't apply.
    wizard_steps: tuple[str, ...] = (
        "normalize", "dedup", "index", "review-label", "auto-name",
        "label", "infer", "review-approval", "move-approved", "build", "viewer",
    )
    # Folder holding already-named sources (spells), used by `infer`. Falls
    # back to ``source_dir`` for flat domains such as items.
    named_source_dir: str | None = None
