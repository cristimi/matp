"""
Prompt template loader from PostgreSQL ai_prompt_templates table.
"""

import logging

import asyncpg

logger = logging.getLogger(__name__)


async def load_template(template_id: str, db_pool: asyncpg.Pool) -> dict | None:
    """
    Load a single template by ID.
    Returns dict with keys: id, name, system_prompt — or None on not found / error.
    """
    try:
        row = await db_pool.fetchrow(
            "SELECT id, name, system_prompt FROM ai_prompt_templates WHERE id = $1",
            template_id,
        )
        if row is None:
            logger.warning("load_template: not found: %s", template_id)
            return None
        return dict(row)
    except Exception as exc:
        logger.warning("load_template error [%s]: %s", template_id, exc)
        return None


async def load_all_templates(db_pool: asyncpg.Pool) -> list[dict]:
    """
    Load all rows from ai_prompt_templates.
    Returns list of dicts, or [] on error.
    """
    try:
        rows = await db_pool.fetch(
            "SELECT id, name, system_prompt FROM ai_prompt_templates ORDER BY id",
        )
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("load_all_templates error: %s", exc)
        return []
