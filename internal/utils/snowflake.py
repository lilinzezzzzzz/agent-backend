from pkg.logger import logger
from pkg.ids import SnowflakeIDGenerator, auto_snowflake_node_id

_snowflake_id_generator: SnowflakeIDGenerator | None = None


def init_snowflake_id_generator(node_id: int | None = None):
    global _snowflake_id_generator
    node_id = node_id or auto_snowflake_node_id()
    _snowflake_id_generator = SnowflakeIDGenerator(node_id)
    logger.success(f"Snowflake ID Generator initialized successfully. Node ID: {node_id}")


def generate_snowflake_id() -> int:
    if _snowflake_id_generator is None:
        raise RuntimeError("Snowflake ID Generator is not initialized")

    return _snowflake_id_generator.generate()


def generate_snowflake_id_batch(count: int) -> list[int]:
    if _snowflake_id_generator is None:
        raise RuntimeError("Snowflake ID Generator is not initialized")

    return _snowflake_id_generator.generate_batch(count)
