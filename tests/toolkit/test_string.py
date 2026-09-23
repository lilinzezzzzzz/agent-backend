from pkg.toolkit.string import (
    escape_control_chars,
    escape_like_pattern,
    mask_string,
)


def test_mask_string_keeps_default_behavior() -> None:
    assert mask_string("sk-abc123xyz789") == "sk-a...z789"
    assert mask_string("abc", show_prefix=2) == "ab..."


def test_mask_string_limits_visible_characters() -> None:
    assert (
        mask_string("sk-abc123xyz789", show_prefix=2, show_suffix=2, max_visible=4)
        == "sk...89"
    )


def test_mask_string_uses_custom_mask() -> None:
    assert (
        mask_string(
            "sk-abc123xyz789", show_prefix=2, show_suffix=2, mask="***", max_visible=4
        )
        == "sk***89"
    )


def test_escape_like_pattern_escapes_wildcards_and_escape_character() -> None:
    assert escape_like_pattern("100%_done") == "100\\%\\_done"
    assert escape_like_pattern("path\\to\\file") == "path\\\\to\\\\file"


def test_escape_like_pattern_keeps_plain_text_and_empty_string() -> None:
    assert escape_like_pattern("plain-text") == "plain-text"
    assert escape_like_pattern("") == ""


def test_escape_control_chars_escapes_common_control_characters() -> None:
    assert escape_control_chars("a\tb\nc\rd") == r"a\tb\nc\rd"


def test_escape_control_chars_escapes_c0_del_and_unicode_line_separators() -> None:
    assert escape_control_chars("a\x00b\x1ec\x7fd") == r"a\x00b\x1ec\x7fd"
    assert escape_control_chars("a\x85b\u2028c\u2029d") == r"a\x85b\u2028c\u2029d"


def test_escape_control_chars_keeps_text_on_one_line() -> None:
    escaped = escape_control_chars("a\nb\rc\x85d\u2028e\u2029f\x0bg")
    assert escaped.splitlines() == [r"a\nb\rc\x85d\u2028e\u2029f\x0bg"]


def test_escape_control_chars_keeps_plain_text_and_empty_string() -> None:
    assert escape_control_chars("plain-text 文本") == "plain-text 文本"
    assert escape_control_chars("") == ""
