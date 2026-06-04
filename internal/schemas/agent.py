from __future__ import annotations

from pydantic import BaseModel, Field

type JsonValue = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


class AgentOrderSupportReqSchema(BaseModel):
    """订单支持 Agent 请求。"""

    question: str = Field(..., description="用户问题", min_length=1, max_length=500)
    max_steps: int = Field(4, description="Agent 最大执行步数", ge=1, le=8)


class AgentStepSchema(BaseModel):
    """Agent 单步执行记录。"""

    index: int = Field(..., description="步骤序号，从 0 开始")
    status: str = Field(..., description="步骤状态")
    decision_type: str = Field(..., description="Decision Maker 决策类型")
    tool: str | None = Field(None, description="被调用的工具名")
    args: dict[str, JsonValue] = Field(default_factory=dict, description="工具调用参数")
    observation: JsonValue = Field(None, description="工具返回 observation")
    error: str | None = Field(None, description="工具或执行错误")
    elapsed_ms: float = Field(..., description="步骤耗时，单位毫秒")


class AgentOrderSupportRespSchema(BaseModel):
    """订单支持 Agent 响应。"""

    run_id: str = Field(..., description="本次 Agent 运行 ID")
    status: str = Field(..., description="Agent 运行状态")
    answer: str | None = Field(None, description="最终回答；达到步数上限时可能为空")
    steps: list[AgentStepSchema] = Field(
        default_factory=list, description="执行步骤记录"
    )
