import pytest

from pkg.toolkit.exc import format_exception_traceback


def _raise_value_error() -> None:
    raise ValueError("invalid input")


def _call_raising_function() -> None:
    _raise_value_error()


def _capture_exception() -> Exception:
    try:
        _call_raising_function()
    except Exception as exc:
        return exc
    raise AssertionError("expected exception was not raised")


def test_format_exception_traceback_limits_tail_without_blank_lines() -> None:
    formatted = format_exception_traceback(_capture_exception(), max_entries=3)

    assert "\n\n" not in formatted
    assert "in _call_raising_function" in formatted
    assert "in _raise_value_error" in formatted
    assert formatted.endswith("ValueError: invalid input")


def test_format_exception_traceback_preserves_full_available_traceback() -> None:
    formatted = format_exception_traceback(_capture_exception(), max_entries=10)

    assert "\n\n" not in formatted
    assert formatted.startswith("Traceback (most recent call last):")
    assert "in _capture_exception" in formatted
    assert "in _call_raising_function" in formatted
    assert "in _raise_value_error" in formatted
    assert formatted.endswith("ValueError: invalid input")


def test_format_exception_traceback_rejects_non_positive_max_entries() -> None:
    with pytest.raises(ValueError, match="max_entries must be greater than zero"):
        format_exception_traceback(_capture_exception(), max_entries=0)
