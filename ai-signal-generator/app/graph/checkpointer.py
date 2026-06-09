import logging

logger = logging.getLogger(__name__)


def get_checkpointer(database_url: str):
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        return AsyncPostgresSaver.from_conn_string(database_url)
    except (ImportError, ModuleNotFoundError):
        logger.warning(
            "langgraph-checkpoint-postgres not installed — using in-memory checkpointer. "
            "Add langgraph-checkpoint-postgres to requirements.txt for persistent checkpointing."
        )
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()
