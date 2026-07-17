import pytest
from opentelemetry.sdk.trace import TracerProvider

from pkg.logger.otel import (
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

    with pytest.raises(ValueError, match="tracer_provider requires enabled tracing"):
        init_tracing(
            enabled=False,
            service_name="test-service",
            tracer_provider=TracerProvider(),
        )
