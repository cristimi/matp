import logging

from app.config import settings

logger = logging.getLogger(__name__)


async def get_available_models(provider: str) -> list[dict]:
    if provider == 'google':
        if not settings.gemini_api_key:
            return []
        try:
            import google.generativeai as genai
            genai.configure(api_key=settings.gemini_api_key)
            models = genai.list_models()
            return [
                {
                    "id": m.name.replace("models/", ""),
                    "display_name": m.display_name,
                    "provider": "google",
                }
                for m in models
                if "generateContent" in m.supported_generation_methods
            ]
        except Exception as exc:
            logger.error("Failed to list Google models: %s", exc)
            return []

    elif provider == 'openai':
        if not settings.openai_api_key:
            return []
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=settings.openai_api_key)
            models = await client.models.list()
            return [
                {
                    "id": m.id,
                    "display_name": m.id,
                    "provider": "openai",
                }
                for m in models.data
                if m.id.startswith("gpt") or m.id.startswith("o1") or m.id.startswith("o3")
            ]
        except Exception as exc:
            logger.error("Failed to list OpenAI models: %s", exc)
            return []

    elif provider == 'anthropic':
        key_configured = bool(settings.anthropic_api_key)
        return [
            {"id": "claude-opus-4-6",   "display_name": "Claude Opus 4.6",   "provider": "anthropic", "key_configured": key_configured},
            {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6", "provider": "anthropic", "key_configured": key_configured},
            {"id": "claude-haiku-4-5",  "display_name": "Claude Haiku 4.5",  "provider": "anthropic", "key_configured": key_configured},
        ]

    return []
