from internal.dao.rag import new_rag_metadata_dao
from internal.factory_cache import reset_factory_caches
from internal.services.user import new_user_service


def test_reset_factory_caches_rebuilds_cached_dependencies() -> None:
    rag_dao = new_rag_metadata_dao()
    user_service = new_user_service()

    assert new_rag_metadata_dao() is rag_dao
    assert new_user_service() is user_service

    reset_factory_caches()

    assert new_rag_metadata_dao() is not rag_dao
    assert new_user_service() is not user_service
