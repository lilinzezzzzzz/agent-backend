from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from pkg.toolkit.json import orjson_dumps, orjson_dumps_bytes, orjson_loads

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore
    HAS_NUMPY = False


class TestOrjsonToolkit:
    """测试 pkg/toolkit/json.py 的核心序列化与反序列化能力。"""

    @pytest.mark.parametrize(
        "input_data, expected_subset",
        [
            (2**53 + 1, 2**53 + 1),
            (42, 42),
            (3.14159, 3.14159),
            (True, True),
            (None, None),
            ({"a", "b"}, ["a", "b"]),
            (b"test_bytes", "test_bytes"),
        ],
    )
    def test_basic_type_round_trip(self, input_data: Any, expected_subset: Any):
        json_str = orjson_dumps({"val": input_data})
        parsed = orjson_loads(json_str)

        if isinstance(input_data, set):
            assert set(parsed["val"]) == input_data
        else:
            assert parsed["val"] == expected_subset

    @pytest.mark.parametrize(
        "decimal_val, expected_type, check_val",
        [
            (Decimal("999.99"), float, 999.99),
            (Decimal("0.0"), float, 0.0),
            (Decimal("0.12345678901234567890"), str, "0.12345678901234567890"),
            (Decimal("1E+20"), str, "1E+20"),
        ],
    )
    def test_decimal_strategy(
        self, decimal_val: Decimal, expected_type: type, check_val: Any
    ):
        res = orjson_loads(orjson_dumps({"d": decimal_val}))
        assert isinstance(res["d"], expected_type)
        if expected_type is str:
            assert str(check_val).lower() in res["d"].lower()
        else:
            assert res["d"] == check_val

    def test_datetime_handling(self):
        dt_utc = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        parsed = orjson_loads(orjson_dumps({"dt": dt_utc}))
        assert parsed["dt"].endswith("+00:00") or parsed["dt"].endswith("Z")

        dt_naive = datetime(2023, 1, 1, 12, 0, 0)
        parsed_naive = orjson_loads(orjson_dumps({"dt": dt_naive}))
        assert "2023-01-01" in parsed_naive["dt"]

    def test_numpy_support(self):
        if not HAS_NUMPY:
            pytest.skip("Numpy not installed")

        data = {
            "arr": np.array([1, 2, 3]),
            "int64": np.int64(9223372036854775807),
            "float32": np.float32(1.5),
        }
        parsed = orjson_loads(orjson_dumps(data))
        assert parsed["arr"] == [1, 2, 3]
        assert parsed["int64"] == 9223372036854775807
        assert parsed["float32"] == 1.5

    def test_dumps_options(self):
        data = {"k": "v"}
        assert isinstance(orjson_dumps(data), str)
        assert isinstance(orjson_dumps_bytes(data), bytes)

    def test_error_handling(self):
        class Unserializable:
            pass

        with pytest.raises(ValueError, match="JSON Serialization Failed"):
            orjson_dumps({"obj": Unserializable()})

        with pytest.raises(ValueError, match="JSON Deserialization Failed"):
            orjson_loads("{invalid_json}")
