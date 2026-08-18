from internal.dao.rag import new_rag_metadata_dao
from internal.factory_cache import reset_factory_caches
from internal.services.logger_span import new_logger_span_service


def test_reset_factory_caches_rebuilds_cached_dependencies() -> None:
    rag_dao = new_rag_metadata_dao()
    logger_span_service = new_logger_span_service()

    assert new_rag_metadata_dao() is rag_dao
    assert new_logger_span_service() is logger_span_service

    reset_factory_caches()

    assert new_rag_metadata_dao() is not rag_dao
    assert new_logger_span_service() is not logger_span_service
