from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

type JsonValue = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


class AgentReqSchema(BaseModel):
    """Agent 通用请求。"""

    question: str = Field(..., description="用户问题", min_length=1, max_length=500)
    max_steps: int = Field(4, description="Agent 最大执行步数", ge=1, le=8)
    confirmation_token: str | None = Field(
        None,
        description="服务端签发的一次性动作确认 token",
        min_length=1,
        max_length=128,
    )
    idempotency_key: str | None = Field(
        None,
        description="确认副作用动作时必填的客户端幂等键",
        min_length=8,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_confirmation_fields(self) -> AgentReqSchema:
        """确认 token 与幂等键必须同时提供。"""
        if (self.confirmation_token is None) != (self.idempotency_key is None):
            raise ValueError(
                "confirmation_token and idempotency_key must be provided together"
            )
        return self


class AgentStepSchema(BaseModel):
    """Agent 单步执行记录。"""

    index: int = Field(..., description="步骤序号，从 0 开始")
    status: str = Field(..., description="步骤状态")
    action_type: str = Field(..., description="Agent 动作类型")
    tool: str | None = Field(None, description="被调用的工具名")
    args: dict[str, JsonValue] = Field(default_factory=dict, description="工具调用参数")
    action_result: JsonValue = Field(None, description="动作执行结果")
    error: str | None = Field(None, description="工具或执行错误")
    elapsed_ms: float = Field(..., description="步骤耗时，单位毫秒")


class AgentActionConfirmationSchema(BaseModel):
    """需要客户端显式确认的服务端动作。"""

    token: str = Field(..., description="短期、一次性的服务端确认 token")
    action: str = Field(..., description="待确认动作名")
    summary: str = Field(..., description="展示给用户确认的动作摘要")
    expires_in_seconds: int = Field(..., description="确认 token 有效期，单位秒")


class AgentRunRespSchema(BaseModel):
    """业务 Agent 运行响应。"""

    run_id: str = Field(..., description="本次 Agent 运行 ID")
    status: str = Field(..., description="Agent 运行状态")
    answer: str | None = Field(None, description="最终回答；达到步数上限时可能为空")
    steps: list[AgentStepSchema] = Field(
        default_factory=list, description="执行步骤记录"
    )
    confirmation: AgentActionConfirmationSchema | None = Field(
        None, description="存在副作用动作时返回的待确认信息"
    )


class AgentOrderSupportReqSchema(AgentReqSchema):
    """订单支持 Agent 请求。"""


class AgentOrderSupportRespSchema(AgentRunRespSchema):
    """订单支持 Agent 响应。"""


class AgentPaymentSupportReqSchema(AgentReqSchema):
    """支付支持 Agent 请求。"""


class AgentPaymentSupportRespSchema(AgentRunRespSchema):
    """支付支持 Agent 响应。"""


class AgentChatReqSchema(AgentReqSchema):
    """统一 Agent 聊天请求。"""


class AgentChatRespSchema(BaseModel):
    """统一 Agent Router 响应。"""

    route: str = Field(..., description="Router 选择的业务域")
    result: AgentRunRespSchema = Field(..., description="业务 Agent 执行结果")
