from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace import TracerProvider

import pkg.tracing.otel as otel_runtime
from pkg.tracing import (
    get_tracer,
    init_tracing,
    is_tracing_enabled,
    shutdown_tracing,
)


@pytest.fixture(autouse=True)
def cleanup_runtime() -> None:
    shutdown_tracing()
    yield
    shutdown_tracing()


def test_disabled_runtime_is_idempotent_and_has_no_tracer() -> None:
    init_tracing(enabled=False, service_name="test-service")
    init_tracing(enabled=False, service_name="test-service")

    assert is_tracing_enabled() is False
    with pytest.raises(RuntimeError, match="Tracing is not enabled"):
        get_tracer()


def test_injected_provider_is_idempotent_and_conflicting_config_fails() -> None:
    provider = TracerProvider()
    try:
        init_tracing(
            enabled=True,
            service_name="test-service",
            tracer_provider=provider,
        )
        init_tracing(
            enabled=True,
            service_name="test-service",
            tracer_provider=provider,
        )

        assert is_tracing_enabled() is True
        assert get_tracer() is not None

        with pytest.raises(RuntimeError, match="different configuration"):
            init_tracing(
                enabled=True,
                service_name="another-service",
                tracer_provider=provider,
            )
    finally:
        shutdown_tracing()
        provider.shutdown()


def test_runtime_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="service_name cannot be empty"):
        init_tracing(enabled=False, service_name="  ")

    disabled_provider = TracerProvider()
    try:
        with pytest.raises(
            ValueError, match="tracer_provider requires enabled tracing"
        ):
            init_tracing(
                enabled=False,
                service_name="test-service",
                tracer_provider=disabled_provider,
            )
    finally:
        disabled_provider.shutdown()

    with pytest.raises(ValueError, match="otlp_traces_endpoint cannot be empty"):
        init_tracing(
            enabled=True,
            service_name="test-service",
            otlp_traces_endpoint="  ",
        )

    with pytest.raises(ValueError, match="greater than zero"):
        init_tracing(
            enabled=True,
            service_name="test-service",
            exporter_timeout_seconds=0,
        )

    injected_provider = TracerProvider()
    try:
        with pytest.raises(ValueError, match="cannot be used with tracer_provider"):
            init_tracing(
                enabled=True,
                service_name="test-service",
                otlp_traces_endpoint="http://collector:4318/v1/traces",
                tracer_provider=injected_provider,
            )
    finally:
        injected_provider.shutdown()


def test_owned_provider_configures_otlp_http_exporter_and_shuts_it_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = MagicMock()
    processor = MagicMock()
    exporter_factory = MagicMock(return_value=exporter)
    processor_factory = MagicMock(return_value=processor)
    installed_provider: list[TracerProvider] = []
    monkeypatch.setattr(otel_runtime, "OTLPSpanExporter", exporter_factory)
    monkeypatch.setattr(otel_runtime, "BatchSpanProcessor", processor_factory)
    monkeypatch.setattr(
        otel_runtime.trace,
        "set_tracer_provider",
        installed_provider.append,
    )
    monkeypatch.setattr(
        otel_runtime.trace,
        "get_tracer_provider",
        lambda: installed_provider[0],
    )

    endpoint = "http://collector:4318/v1/traces"
    init_tracing(
        enabled=True,
        service_name="test-service",
        otlp_traces_endpoint=endpoint,
        exporter_timeout_seconds=3.5,
    )
    init_tracing(
        enabled=True,
        service_name="test-service",
        otlp_traces_endpoint=endpoint,
        exporter_timeout_seconds=3.5,
    )

    exporter_factory.assert_called_once_with(endpoint=endpoint, timeout=3.5)
    processor_factory.assert_called_once_with(exporter)

    with pytest.raises(RuntimeError, match="different configuration"):
        init_tracing(
            enabled=True,
            service_name="test-service",
            otlp_traces_endpoint=endpoint,
            exporter_timeout_seconds=5.0,
        )

    shutdown_tracing()
    processor.shutdown.assert_called_once_with()
