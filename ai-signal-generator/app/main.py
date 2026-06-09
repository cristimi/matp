"""
AI Signal Generator Service — FastAPI application entry point.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.database import init_db, get_pool
from app.prompt.builder import build_prompt, get_estimated_tokens

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="MATP AI Signal Generator",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ai-signal-generator"}


class PreviewPromptRequest(BaseModel):
    strategy_id: str
    mock_state: dict


@app.post("/internal/preview-prompt")
async def internal_preview_prompt(body: PreviewPromptRequest):
    try:
        pool   = get_pool()
        prompt = await build_prompt(body.mock_state, pool)
        return {
            "prompt":           prompt,
            "estimated_tokens": get_estimated_tokens(prompt),
        }
    except Exception as exc:
        logger.error("preview-prompt error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
