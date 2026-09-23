import re
import uuid
from string import Template
from urllib.parse import urlencode, urlunparse

import xxhash


def hash_to_int(data: str) -> int:
    return xxhash.xxh64_intdigest(data)


# 生成唯一的文件名
def generate_unique_filename(filename: str) -> str:
    return f"{uuid.uuid4().hex}_{filename}"


def template_substitute(template: Template | str, safe: bool = False, **kwargs) -> str:
    """
    使用字符串模板替换变量。

    :param template: 字符串模板，包含变量占位符
    :param kwargs: 替换变量的字典
    :param safe: 是否使用安全模式
    :return: 替换后的字符串

    使用示例：
        >>> result = template_substitute(Template("Hello {name}, you are {age} years old"), name="Alice", age=25)
        >>> print(result)
        Hello Alice, you are 25 years old
    """
    if isinstance(template, str):
        template = Template(template)

    if safe:
        return template.safe_substitute(**kwargs)

    return template.substitute(**kwargs)


def validate_phone_number(phone: str) -> bool:
    """
    校验手机号是否符合中国大陆的手机号格式
    :param phone: 待验证的手机号字符串
    :return: 如果手机号格式正确，返回 True，否则返回 False
    """
    # 正则表达式：以1开头，第二位是3-9之间的数字，后面是9个数字
    pattern = r"^1[3-9]\d{9}$"

    return bool(re.match(pattern, phone))


def build_url(
    scheme: str = "http",
    netloc: str = "localhost",
    path: str = "/",
    query: dict | None = None,  # 仅支持字典或 None
    fragment: str = "",
):
    """
    构建一个 URL，使用默认值填充缺失的部分。

    :param scheme: URL 协议（默认为 "http"）
    :param netloc: 网络位置（默认为 "localhost"）
    :param path: 资源路径（默认为 "/"）
    :param query: 查询字符串，必须是字典类型或 None（默认为 None）
    :param fragment: 片段标识符（默认为 ""）
    :return: 组装后的完整 URL

    示例：
    >>> build_url(scheme="https", path="api", query={"page": 2, "sort": "desc"})
    'https://localhost/api?page=2&sort=desc'

    >>> build_url(path="search", query={"q": "python"})
    'http://localhost/search?q=python'

    >>> build_url(path="profile")
    'http://localhost/profile'
    """
    # 处理 path，确保以 `/` 开头
    path = "/" + path.lstrip("/") if path else "/"

    # 处理 query（如果是字典，转换为 URL 编码字符串）
    if query is None:
        query_string = ""
    elif isinstance(query, dict):
        query_string = urlencode(query)
    else:
        raise ValueError("query must be none or dict")

    # 根据 scheme 设定默认 netloc
    if not netloc:
        netloc = "localhost:443" if scheme == "https" else "localhost:80"

    # 组装 URL（去掉 params 参数）
    return urlunparse((scheme, netloc, path, "", query_string, fragment))


def mask_string(
    text: str,
    show_prefix: int = 4,
    show_suffix: int = 4,
    min_length: int = 8,
    mask: str = "...",
    max_visible: int | None = None,
) -> str:
    """字符串脱敏

    Args:
        text: 需要脱敏的字符串
        show_prefix: 显示前几位，默认 4
        show_suffix: 显示后几位，默认 4
        min_length: 最小长度阈值，低于此值只显示前 2 位
        mask: 脱敏占位符，默认 ...
        max_visible: 最大明文字符数，默认不限制

    Returns:
        脱敏后的字符串

    Examples:
        >>> mask_string("sk-abc123xyz789")
        'sk-a...z789'
        >>> mask_string("abc", show_prefix=2)
        'ab...'
    """
    if not text:
        return ""

    if len(text) <= min_length:
        prefix_length = min(2, show_prefix, len(text))
        if max_visible is not None:
            prefix_length = min(prefix_length, max_visible)
        return f"{text[:prefix_length]}{mask}"

    prefix_length = min(show_prefix, len(text))
    suffix_length = min(show_suffix, max(0, len(text) - prefix_length))
    if max_visible is not None:
        prefix_length = min(prefix_length, max_visible)
        suffix_length = min(suffix_length, max(0, max_visible - prefix_length))

    suffix = text[-suffix_length:] if suffix_length else ""
    return f"{text[:prefix_length]}{mask}{suffix}"


def escape_like_pattern(text: str, escape_char: str = "\\") -> str:
    """转义 SQL LIKE 查询中的特殊字符。

    LIKE 模式中 % 和 _ 是通配符，需要进行转义以实现精确匹配。

    Args:
        text: 需要转义的字符串
        escape_char: 转义字符，默认为反斜杠

    Returns:
        转义后的字符串

    Examples:
        >>> escape_like_pattern("test_1")
        'test\\_1'
        >>> escape_like_pattern("100%")
        '100\\%'
        >>> escape_like_pattern("path\\to\\file")
        'path\\\\to\\\\file'
    """
    if not text:
        return ""
    # 先转义转义字符本身，再转义 % 和 _
    return (
        text.replace(escape_char, escape_char * 2)
        .replace("%", f"{escape_char}%")
        .replace("_", f"{escape_char}_")
    )


# 按「字段: 值」逐行呈现的消息（如告警、通知）中，未转义的换行可让不可信输入
# 伪造字段行；把控制字符统一转义为可见形式后，信息保留但文本无法越行。
# \x85、\u2028、\u2029 不在 C0 控制区内，但 str.splitlines() 同样视作换行边界。
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]")
_CONTROL_CHAR_ESCAPES = {"\t": "\\t", "\n": "\\n", "\r": "\\r"}


def _escape_control_char(match: re.Match[str]) -> str:
    char = match.group()
    escaped = _CONTROL_CHAR_ESCAPES.get(char)
    if escaped is not None:
        return escaped
    codepoint = ord(char)
    if codepoint > 0xFF:
        return f"\\u{codepoint:04x}"
    return f"\\x{codepoint:02x}"


def escape_control_chars(value: str) -> str:
    r"""把字符串中的控制字符转义为可见形式。

    面向按「字段: 值」逐行呈现的告警或单行日志字段：不可信文本中的换行会让
    调用方伪造字段行，转义后信息保留但无法越行。结果仅用于展示，不保证可逆
    （反斜杠本身不转义）。

    Args:
        value: 可能携带外部输入或异常内容的文本。

    Returns:
        控制字符转义后的文本；适合插入按行解析的告警或单行日志字段。

    Examples:
        >>> escape_control_chars("line1\nline2")
        'line1\\nline2'
    """
    return _CONTROL_CHAR_PATTERN.sub(_escape_control_char, value)
