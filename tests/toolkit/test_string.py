from pkg.toolkit.string import mask_string


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
