"""Backward-compatible re-exports for the item taxonomy/prompts.

The canonical definitions now live in :mod:`lib.domains.items` so each asset
family owns its own vocabulary. Existing imports from ``lib.llm.taxonomy`` keep
working, but new code should resolve a :class:`~lib.domains.base.Domain` and use
``domain.prompts`` / ``domain.taxonomy`` instead.
"""
from lib.domains.items import (
    SYSTEM_PROMPT,
    TAG_TAXONOMY,
    TEXT_SYSTEM_PROMPT,
    TEXT_USER_PROMPT_TEMPLATE,
    USER_PROMPT_TEMPLATE,
)

__all__ = [
    "SYSTEM_PROMPT",
    "TAG_TAXONOMY",
    "TEXT_SYSTEM_PROMPT",
    "TEXT_USER_PROMPT_TEMPLATE",
    "USER_PROMPT_TEMPLATE",
]
