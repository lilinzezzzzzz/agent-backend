from threading import get_ident
from unittest.mock import MagicMock

import grpc
import pytest
from pymilvus import MilvusClient

import pkg.vectors.backends.milvus.backend as backend_module
from pkg.vectors.backends.milvus import MilvusBackend
from pkg.vectors.backends.milvus.types import FullTextSearchSpec, MilvusCollectionSpec
from pkg.vectors.contracts import (
    FilterCondition,
    FilterOperator,
    RetrievalMode,
    SearchRequest,
)


@pytest.fixture
def collection_spec() -> MilvusCollectionSpec:
    return MilvusCollectionSpec(
        name="timeout_test",
        dimension=2,
        full_text_search=FullTextSearchSpec(enabled=True),
    )


@pytest.fixture
def sdk_client() -> MagicMock:
    client = MagicMock(spec=MilvusClient)
    client.has_collection.return_value = True
    client.query.return_value = []
    client.search.return_value = [[]]
    client.hybrid_search.return_value = [[]]
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [7, 2.5])
@pytest.mark.parametrize("use_filter", [False, True])
async def test_fetch_passes_timeout_to_collection_checks_and_query(
    timeout: float,
    use_filter: bool,
    collection_spec: MilvusCollectionSpec,
    sdk_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor = MagicMock(return_value=sdk_client)
    monkeypatch.setattr(backend_module, "MilvusClient", constructor)
    backend = MilvusBackend(uri="http://test-milvus:19530", timeout=timeout)
    filters = (
        [FilterCondition(field="id", op=FilterOperator.IN, value=[1])]
        if use_filter
        else None
    )

    assert (
        await backend.fetch(
            spec=collection_spec,
            ids=None if use_filter else [1],
            filters=filters,
        )
        == []
    )

    assert constructor.call_args.kwargs["timeout"] == timeout
    for method in (
        sdk_client.has_collection,
        sdk_client.load_collection,
        sdk_client.query,
    ):
        method.assert_called_once()
        assert method.call_args.kwargs["timeout"] == timeout


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", [RetrievalMode.DENSE, RetrievalMode.FULL_TEXT, RetrievalMode.HYBRID]
)
async def test_search_passes_timeout_for_each_retrieval_mode(
    mode: RetrievalMode,
    collection_spec: MilvusCollectionSpec,
    sdk_client: MagicMock,
) -> None:
    backend = MilvusBackend(uri="http://test-milvus:19530", timeout=9)
    backend._client = sdk_client
    request = SearchRequest(
        vector=None if mode == RetrievalMode.FULL_TEXT else [0.1, 0.2],
        query_text=None if mode == RetrievalMode.DENSE else "test query",
        retrieval_mode=mode,
        top_k=2,
    )
    assert await backend.search(spec=collection_spec, request=request) == []
    rpc = (
        sdk_client.hybrid_search if mode == RetrievalMode.HYBRID else sdk_client.search
    )
    rpc.assert_called_once()
    assert rpc.call_args.kwargs["timeout"] == 9
    assert sdk_client.has_collection.call_args.kwargs["timeout"] == 9
    assert sdk_client.load_collection.call_args.kwargs["timeout"] == 9


class RpcFailure(grpc.RpcError):
    def __init__(self, status: grpc.StatusCode) -> None:
        super().__init__("simulated RPC failure")
        self.status = status

    def code(self) -> grpc.StatusCode:
        return self.status


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_succeeds", [True, False])
async def test_recovery_reuses_timeout_and_propagates_persistent_deadline(
    recovery_succeeds: bool,
    collection_spec: MilvusCollectionSpec,
    sdk_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MilvusBackend(uri="http://test-milvus:19530", timeout=7)
    backend._client = sdk_client
    sdk_client.query.side_effect = RpcFailure(grpc.StatusCode.DEADLINE_EXCEEDED)
    replacement = MagicMock(spec=MilvusClient)
    replacement.has_collection.return_value = True
    replacement.query.return_value = []
    persistent_error = RpcFailure(grpc.StatusCode.DEADLINE_EXCEEDED)
    if not recovery_succeeds:
        replacement.query.side_effect = persistent_error
    constructor = MagicMock(return_value=replacement)
    monkeypatch.setattr(backend_module, "MilvusClient", constructor)

    if recovery_succeeds:
        assert await backend.fetch(spec=collection_spec, ids=[1]) == []
    else:
        with pytest.raises(RpcFailure) as raised:
            await backend.fetch(spec=collection_spec, ids=[1])
        assert raised.value is persistent_error

    sdk_client.close.assert_called_once_with()
    constructor.assert_called_once()
    assert constructor.call_args.kwargs["timeout"] == 7
    for client in (sdk_client, replacement):
        for method in (client.has_collection, client.load_collection, client.query):
            method.assert_called_once()
            assert method.call_args.kwargs["timeout"] == 7


@pytest.mark.asyncio
async def test_rpc_still_runs_in_worker_thread(sdk_client: MagicMock) -> None:
    event_loop_thread = get_ident()

    def get_version(timeout: float) -> str:
        assert get_ident() != event_loop_thread
        assert timeout == 7
        return "test-version"

    sdk_client.get_server_version.side_effect = get_version
    backend = MilvusBackend(uri="http://test-milvus:19530", timeout=7)
    backend._client = sdk_client
    assert await backend.healthcheck() == {
        "backend": "milvus",
        "status": "ok",
        "version": "test-version",
    }


@pytest.mark.asyncio
async def test_unconfigured_timeout_keeps_sdk_default(sdk_client: MagicMock) -> None:
    backend = MilvusBackend(uri="http://test-milvus:19530")
    backend._client = sdk_client
    await backend.release_collection(collection_name="timeout_test")
    sdk_client.release_collection.assert_called_once_with(
        collection_name="timeout_test"
    )
