import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from pkg.lazy_proxy import lazy_proxy
from pkg.smart_types import (
    JS_MAX_SAFE_INTEGER,
    IntStr,
    SmartDatetime,
    SmartDecimal,
    SmartInt,
)


# 定义一个用于测试的模型
class DemoModel(BaseModel):
    id: SmartInt | None = None
    score: SmartDecimal | None = None
    created_at: SmartDatetime | None = None


class IntStrModel(BaseModel):
    value: IntStr


# ==========================================
# 1. 测试 SmartInt
# ==========================================
class TestSmartInt:

    def test_smart_int_safe_range(self):
        """测试 JS 安全范围内的整数 -> JSON 保持数字"""
        input_val = 12345
        model = DemoModel(id=input_val)

        # 验证内部类型
        assert isinstance(model.id, int)
        assert model.id == 12345

        # 验证 JSON 输出 (保持 int)
        json_data = json.loads(model.model_dump_json())
        assert json_data['id'] == 12345
        assert isinstance(json_data['id'], int)

    def test_smart_int_unsafe_range(self):
        """测试超过 JS 安全范围的整数 -> JSON 转为字符串"""
        # 构造一个比 JS 最大安全整数大 1 的数
        unsafe_val = JS_MAX_SAFE_INTEGER + 1
        model = DemoModel(id=unsafe_val)

        # 验证内部类型 (依然是 int，方便 Python 计算)
        assert isinstance(model.id, int)
        assert model.id == unsafe_val

        # 验证 JSON 输出 (转为 str)
        json_data = json.loads(model.model_dump_json())
        assert json_data['id'] == str(unsafe_val)
        assert isinstance(json_data['id'], str)

    def test_smart_int_from_string(self):
        """测试前端传字符串数字 -> 内部转 int"""
        model = DemoModel(id="999")
        assert model.id == 999
        assert isinstance(model.id, int)

    @pytest.mark.parametrize("value", [1, "1"])
    def test_smart_int_accepts_minimum(self, value):
        """测试最小值 1 可通过校验"""
        assert DemoModel(id=value).id == 1

    @pytest.mark.parametrize("value", [0, "0", -1, "-1"])
    def test_smart_int_rejects_values_below_minimum(self, value):
        """测试拒绝小于 1 的整数和数字字符串"""
        with pytest.raises(ValidationError):
            DemoModel(id=value)

    def test_smart_int_invalid(self):
        """测试非法输入"""
        with pytest.raises(ValidationError):
            DemoModel(id="not-a-number")

    def test_smart_int_reject_bool(self):
        """测试拒绝 bool 类型输入"""
        with pytest.raises(ValidationError):
            DemoModel(id=True)
        with pytest.raises(ValidationError):
            DemoModel(id=False)

    def test_smart_int_json_schema_declares_minimum(self):
        """测试 JSON Schema 与运行时最小值约束一致"""
        schema = TypeAdapter(SmartInt).json_schema()
        assert schema["anyOf"] == [
            {"minimum": 1, "type": "integer"},
            {"pattern": r"^\+?0*[1-9]\d*$", "type": "string"},
        ]


# ==========================================
# 2. 测试 SmartDecimal
# ==========================================
class TestSmartDecimal:

    def test_smart_decimal_simple(self):
        """测试简单浮点数 (精度低) -> JSON 转 float"""
        # 输入字符串以避免 float 初始化的精度干扰，模拟前端 JSON 传参
        model = DemoModel(score="10.5")

        # 验证内部类型
        assert isinstance(model.score, Decimal)
        assert model.score == Decimal("10.5")

        # 验证 JSON 输出 (转为 float)
        json_data = json.loads(model.model_dump_json())
        assert json_data['score'] == 10.5
        assert isinstance(json_data['score'], float)

    def test_smart_decimal_from_int(self):
        """测试整数输入 -> 内部转 Decimal"""
        model = DemoModel(score=100)
        assert isinstance(model.score, Decimal)
        assert model.score == Decimal("100")

        json_data = json.loads(model.model_dump_json())
        assert json_data['score'] == 100.0
        assert isinstance(json_data['score'], float)

    def test_smart_decimal_reject_bool(self):
        """测试拒绝 bool 类型输入"""
        with pytest.raises(ValidationError):
            DemoModel(score=True)
        with pytest.raises(ValidationError):
            DemoModel(score=False)

    def test_smart_decimal_high_precision(self):
        """测试高精度小数 (>6位小数) -> JSON 转 str"""
        val = "0.1234567"  # 7位小数
        model = DemoModel(score=val)

        # 验证内部类型
        assert isinstance(model.score, Decimal)

        # 验证 JSON 输出 (保留精度，转为 str)
        json_data = json.loads(model.model_dump_json())
        assert json_data['score'] == val
        assert isinstance(json_data['score'], str)

    def test_smart_decimal_large_number(self):
        """测试超大数值 -> JSON 转 str"""
        val = "10000000000000001"  # 超过 1e16
        model = DemoModel(score=val)

        json_data = json.loads(model.model_dump_json())
        assert json_data['score'] == val
        assert isinstance(json_data['score'], str)


# ==========================================
# 3. 测试 SmartDatetime
# ==========================================
class TestSmartDatetime:

    def test_smart_datetime_from_iso_string(self):
        """测试 ISO 字符串输入 (带 Z)"""
        input_str = "2025-12-09T10:30:00Z"
        model = DemoModel(created_at=input_str)

        # 验证内部类型 (datetime, 无时区)
        assert isinstance(model.created_at, datetime)
        assert model.created_at.year == 2025
        assert model.created_at.tzinfo is None

        # 验证 JSON 输出 (使用 Z 格式表示 UTC)
        json_data = json.loads(model.model_dump_json())
        assert json_data['created_at'] == "2025-12-09T10:30:00Z"

    def test_smart_datetime_from_obj(self):
        """测试直接传 datetime 对象"""
        dt = datetime(2025, 5, 20, 13, 14, 0)
        model = DemoModel(created_at=dt)

        assert model.created_at == dt

        json_data = json.loads(model.model_dump_json())
        assert json_data['created_at'] == "2025-05-20T13:14:00Z"

    def test_smart_datetime_invalid(self):
        """测试非法日期格式"""
        with pytest.raises(ValidationError):
            DemoModel(created_at="invalid-date-format")

    def test_smart_datetime_none_output(self):
        """测试 Optional[SmartDatetime] 为 None 时正确输出 null"""
        model = DemoModel(created_at=None)
        json_data = json.loads(model.model_dump_json())
        assert json_data['created_at'] is None

    def test_smart_datetime_with_timezone(self):
        """测试带时区的 datetime 输入"""
        # 东八区时间
        dt = datetime(2025, 5, 20, 21, 0, 0, tzinfo=UTC)  # UTC 时间
        model = DemoModel(created_at=dt)

        # 内部应为 naive datetime (UTC)
        assert model.created_at.tzinfo is None
        assert model.created_at.hour == 21


# ==========================================
# 4. 测试 IntStr
# ==========================================
class TestIntStr:

    def test_int_str_from_int(self):
        """测试整数输入 -> 内部转 str"""
        model = IntStrModel(value=123456)
        assert isinstance(model.value, str)
        assert model.value == "123456"

    def test_int_str_from_str(self):
        """测试字符串输入 -> 保持 str"""
        model = IntStrModel(value="789012")
        assert model.value == "789012"

    def test_int_str_reject_bool(self):
        """测试拒绝 bool 类型"""
        with pytest.raises(ValidationError):
            IntStrModel(value=True)
        with pytest.raises(ValidationError):
            IntStrModel(value=False)

    def test_int_str_reject_none(self):
        """测试拒绝 None"""
        with pytest.raises(ValidationError):
            IntStrModel(value=None)

    def test_int_str_reject_list(self):
        """测试拒绝 list 类型"""
        with pytest.raises(ValidationError):
            IntStrModel(value=[1, 2, 3])

    def test_int_str_reject_dict(self):
        """测试拒绝 dict 类型"""
        with pytest.raises(ValidationError):
            IntStrModel(value={"a": 1})


# ==========================================
# 5. 测试 LazyProxy
# ==========================================
class TestLazyProxy:

    def test_lazy_proxy_deferred_init(self):
        """测试延迟初始化"""
        call_count = 0

        def get_value():
            nonlocal call_count
            call_count += 1
            return "initialized"

        proxy = lazy_proxy(get_value)
        # 此时还未调用 getter
        assert call_count == 0

        # 访问属性时才调用
        _ = proxy.upper()
        assert call_count == 1

    def test_lazy_proxy_type_inference(self):
        """测试类型推断"""
        def get_str() -> str:
            return "hello"

        proxy = lazy_proxy(get_str)
        # proxy 应该被推断为 str 类型
        assert proxy.upper() == "HELLO"
        assert proxy.startswith("he")

    def test_lazy_proxy_repr(self):
        """测试 __repr__ 方法"""
        def get_value():
            return 42

        proxy = lazy_proxy(get_value)
        assert repr(proxy) == "42"

    def test_lazy_proxy_repr_uninitialized(self):
        """测试未初始化时的 repr"""
        def get_value():
            raise RuntimeError("Not initialized")

        proxy = lazy_proxy(get_value)
        assert repr(proxy) == "<_LazyProxy: uninitialized>"
