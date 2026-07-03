from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic
from typing import Any

from pydantic import BaseModel

from internal.dao.agent_audit import AgentAuditDao, new_agent_audit_dao
from internal.models.agent_audit import AgentAudit
from internal.schemas.agent import JsonValue
from internal.schemas.agent import AgentRunResultDTO
from internal.utils.background_tasks import background_task_manager
from pkg.logger import logger
from pkg import request_context as context
from pkg.toolkit.string import mask_string
from pkg.toolkit.timer import utc_now_naive

_REDACTED = "[REDACTED]"
_MASK = "..."
_AUDIT_TASK_TIMEOUT_SECONDS = 10
_EMAIL_RE = re.compile(
    r"(?P<name>[A-Z0-9._%+-]+)@(?P<domain>[A-Z0-9.-]+\.[A-Z]{2,})", re.IGNORECASE
)
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_CREDENTIAL_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "jwt",
    "password",
    "access_token",
    "refresh_token",
    "secret",
    "session",
    "token",
)
_MASKED_KEY_PARTS = (
    "confirmation_token",
    "email",
    "idempotency_key",
    "phone",
    "tax_no",
)
_NON_SECRET_TOKEN_KEYS = {
    "max_tokens",
    "max_completion_tokens",
    "completion_tokens",
    "prompt_tokens",
    "total_tokens",
}


@dataclass(slots=True)
class AgentAuditContext:
    """单次 Agent Service 调用的审计上下文。"""

    agent_name: str
    user_id: int
    user_input: str
    max_steps: int
    trace_id: str | None = None
    started_at: datetime = field(default_factory=utc_now_naive)
    _monotonic_started_at: float = field(default_factory=monotonic)
    llm_calls: list[dict[str, JsonValue]] = field(default_factory=list)

    @classmethod
    def start(
        cls,
        *,
        agent_name: str,
        user_id: int,
        user_input: str,
        max_steps: int,
    ) -> AgentAuditContext:
        """创建审计上下文并读取当前 trace_id。"""
        return cls(
            agent_name=agent_name,
            user_id=user_id,
            user_input=user_input,
            max_steps=max_steps,
            trace_id=_safe_get_trace_id(),
        )

    def elapsed_ms(self) -> float:
        """返回从审计上下文创建到当前的耗时。"""
        return round((monotonic() - self._monotonic_started_at) * 1000, 3)

    def add_llm_call(self, call: Mapping[str, Any]) -> None:
        """追加一条脱敏后的 LLM 调用记录。"""
        self.llm_calls.append(to_json_object(redact_value(call)))


class AgentAuditService:
    """Agent 运行审计写入服务。"""

    def __init__(self, *, audit_dao: AgentAuditDao):
        self._audit_dao = audit_dao

    async def record_agent_run(
        self,
        *,
        agent_name: str,
        user_id: int,
        trace_id: str | None,
        user_input: str,
        max_steps: int,
        result: AgentRunResultDTO,
        started_at: datetime,
        ended_at: datetime,
        llm_calls: Sequence[Mapping[str, JsonValue]],
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> bool:
        """持久化一条 Agent 运行审计记录，失败时不影响业务响应。"""
        try:
            audit = AgentAudit.create(
                run_id=result.run_id,
                agent_name=agent_name,
                user_id=user_id,
                trace_id=trace_id,
                user_input=str(redact_value(user_input)),
                max_steps=max_steps,
                status=result.status,
                final_answer=(
                    str(redact_value(result.answer))
                    if result.answer is not None
                    else None
                ),
                started_at=started_at,
                ended_at=ended_at,
                elapsed_ms=round((ended_at - started_at).total_seconds() * 1000, 3),
                steps=[_step_to_audit_payload(step) for step in result.steps],
                llm_calls=[to_json_object(redact_value(call)) for call in llm_calls],
                audit_metadata=to_json_object(redact_value(metadata or {})),
                creator_id=user_id,
            )
            await self._audit_dao.insert(audit)
            return True
        except Exception as exc:
            logger.warning(f"Agent audit write failed: {type(exc).__name__}")
            return False


class AuditedAgentLLMClient:
    """包装 LLM client，采集结构化调用的 prompt、model 和响应。"""

    def __init__(self, *, llm_client: Any, audit_context: AgentAuditContext):
        self._llm_client = llm_client
        self._audit_context = audit_context
        self.model = getattr(llm_client, "model", None)
        self.provider = getattr(llm_client, "provider", None)

    async def chat_completion_structured[StructuredOutputT: BaseModel](
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_model: type[StructuredOutputT],
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> StructuredOutputT:
        """调用底层 LLM client，并记录结构化审计事件。"""
        started_at = utc_now_naive()
        monotonic_started_at = monotonic()
        raw_response: JsonValue = None

        def audit_hook(response: Any) -> None:
            nonlocal raw_response
            raw_response = to_json_value(redact_value(response))

        call_kwargs = dict(kwargs)
        if _supports_openai_audit_hook(self._llm_client):
            call_kwargs["_audit_hook"] = audit_hook

        try:
            parsed = await self._llm_client.chat_completion_structured(
                messages=messages,
                response_model=response_model,
                max_tokens=max_tokens,
                temperature=temperature,
                **call_kwargs,
            )
        except Exception as exc:
            ended_at = utc_now_naive()
            self._audit_context.add_llm_call(
                {
                    "provider": self.provider,
                    "model": self.model,
                    "response_model": response_model.__name__,
                    "started_at": started_at.isoformat(),
                    "ended_at": ended_at.isoformat(),
                    "elapsed_ms": round((monotonic() - monotonic_started_at) * 1000, 3),
                    "request": _llm_request_payload(
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        kwargs=kwargs,
                    ),
                    "raw_response": raw_response,
                    "parsed_response": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise

        ended_at = utc_now_naive()
        self._audit_context.add_llm_call(
            {
                "provider": self.provider,
                "model": self.model,
                "response_model": response_model.__name__,
                "started_at": started_at.isoformat(),
                "ended_at": ended_at.isoformat(),
                "elapsed_ms": round((monotonic() - monotonic_started_at) * 1000, 3),
                "request": _llm_request_payload(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    kwargs=kwargs,
                ),
                "raw_response": raw_response,
                "parsed_response": _model_to_json_value(parsed),
                "error": None,
            }
        )
        return parsed


async def record_agent_audit(
    *,
    audit_writer: AgentAuditService,
    audit_context: AgentAuditContext,
    result: AgentRunResultDTO,
    metadata: Mapping[str, JsonValue] | None = None,
) -> bool:
    """性能型审计写入入口：请求链路只投递后台任务。"""
    audit_metadata = dict(result.audit_metadata or {})
    if metadata is not None:
        audit_metadata.update(metadata)
    audit_kwargs = {
        "agent_name": audit_context.agent_name,
        "user_id": audit_context.user_id,
        "trace_id": audit_context.trace_id,
        "user_input": audit_context.user_input,
        "max_steps": audit_context.max_steps,
        "result": result,
        "started_at": audit_context.started_at,
        "ended_at": utc_now_naive(),
        "llm_calls": audit_context.llm_calls,
        "metadata": audit_metadata,
    }

    # 当前实现是性能优先的 best-effort 审计：后台任务管理器只提供进程内后台执行，
    # 进程退出、队列溢出或任务取消时可能丢审计。强审计场景需要升级为同步写一条
    # 最小 outbox 事件，再由后台 worker/Celery 消费并写完整审计，配套重试、幂等和失败补偿。
    try:
        return await background_task_manager.add_task(
            f"agent_audit:{audit_context.agent_name}:{result.run_id}",
            coro_func=audit_writer.record_agent_run,
            kwargs_dict=audit_kwargs,
            timeout=_AUDIT_TASK_TIMEOUT_SECONDS,
        )
    except RuntimeError as exc:
        if not _is_background_task_manager_unavailable(exc):
            logger.warning(f"Agent audit enqueue failed: {type(exc).__name__}")
            return False
        try:
            return await audit_writer.record_agent_run(**audit_kwargs)
        except Exception as write_exc:
            logger.warning(f"Agent audit write failed: {type(write_exc).__name__}")
            return False
    except Exception as exc:
        logger.warning(f"Agent audit enqueue failed: {type(exc).__name__}")
        return False


def failed_agent_result(
    *,
    run_id: str,
    error_type: str,
    message: str | None = None,
) -> AgentRunResultDTO:
    """构造用于审计的失败结果，不暴露给 API。"""
    metadata_message = f"{error_type}: {message}" if message else error_type
    return AgentRunResultDTO(
        run_id=run_id,
        status="failed",
        answer=None,
        steps=[],
        audit_metadata={"error": metadata_message},
    )


def redact_value(value: Any) -> Any:
    """递归脱敏审计载荷。"""
    if isinstance(value, BaseModel):
        return redact_value(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _is_credential_key(key_str):
                redacted[key_str] = _REDACTED
            elif _is_masked_key(key_str):
                redacted[key_str] = _mask_sensitive_value(item)
            else:
                redacted[key_str] = redact_value(item)
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if hasattr(value, "__dict__"):
        return redact_value(vars(value))
    return value


def to_json_value(value: Any) -> JsonValue:
    """把任意值压缩成 JSON 可持久化值。"""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [to_json_value(item) for item in value]
    if isinstance(value, BaseModel):
        return to_json_value(value.model_dump(mode="json"))
    if hasattr(value, "__dict__"):
        return to_json_value(vars(value))
    return str(value)


def to_json_object(value: Any) -> dict[str, JsonValue]:
    """把任意 mapping 转成 JSON object。"""
    if not isinstance(value, Mapping):
        return {}
    return {str(key): to_json_value(item) for key, item in value.items()}


def _step_to_audit_payload(step: Any) -> dict[str, JsonValue]:
    return {
        "index": step.index,
        "status": step.status,
        "action_type": step.action_type,
        "tool": step.tool,
        "args": to_json_value(redact_value(step.args)),
        "action_result": to_json_value(redact_value(step.action_result)),
        "error": step.error,
        "elapsed_ms": step.elapsed_ms,
    }


def _llm_request_payload(
    *,
    messages: Sequence[Mapping[str, Any]],
    max_tokens: int | None,
    temperature: float | None,
    kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "messages": list(messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "extra": dict(kwargs),
    }


def _model_to_json_value(value: BaseModel) -> JsonValue:
    return to_json_value(redact_value(value.model_dump(mode="json", exclude_none=True)))


def _safe_get_trace_id() -> str | None:
    try:
        return context.get_trace_id()
    except LookupError:
        return None


def _normalize_key(key: str) -> str:
    return key.lower().replace("-", "_")


def _is_credential_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in _NON_SECRET_TOKEN_KEYS:
        return False
    if any(part in normalized for part in _MASKED_KEY_PARTS):
        return False
    return any(part in normalized for part in _CREDENTIAL_KEY_PARTS)


def _is_masked_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in _NON_SECRET_TOKEN_KEYS:
        return False
    return any(part in normalized for part in _MASKED_KEY_PARTS)


def _mask_sensitive_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return _mask_text(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_mask_sensitive_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _mask_sensitive_value(item) for key, item in value.items()}
    return _REDACTED


def _mask_text(value: str) -> str:
    return mask_string(
        value,
        show_prefix=2,
        show_suffix=4,
        min_length=4,
        mask=_MASK,
        max_visible=6,
    )


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _NON_SECRET_TOKEN_KEYS:
        return False
    return _is_credential_key(key) or _is_masked_key(key)


def _redact_text(value: str) -> str:
    value = _EMAIL_RE.sub(lambda match: _mask_text(match.group(0)), value)
    return _PHONE_RE.sub(lambda match: _mask_text(match.group(0)), value)


def _supports_openai_audit_hook(llm_client: Any) -> bool:
    return llm_client.__class__.__name__ == "OpenAIClient"


def _is_background_task_manager_unavailable(exc: RuntimeError) -> bool:
    message = str(exc)
    return "not initialized" in message or "not started" in message


_agent_audit_service: AgentAuditService | None = None


def new_agent_audit_service() -> AgentAuditService:
    """依赖注入：获取 AgentAuditService 单例。"""
    global _agent_audit_service
    if _agent_audit_service is None:
        _agent_audit_service = AgentAuditService(audit_dao=new_agent_audit_dao())
    return _agent_audit_service
