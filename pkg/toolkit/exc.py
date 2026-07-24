import traceback


def format_exception_traceback(exc: Exception, *, max_entries: int = 5) -> str:
    """格式化异常 traceback，最多保留末尾指定数量的格式化片段。"""
    if max_entries <= 0:
        raise ValueError("max_entries must be greater than zero")

    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    selected_lines = (
        tb_lines[-max_entries:] if len(tb_lines) >= max_entries else tb_lines
    )
    return "".join(selected_lines).strip()
