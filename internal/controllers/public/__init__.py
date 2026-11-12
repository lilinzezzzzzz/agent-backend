"""常规应用的匿名 API 分组，实验路由由独立 demo app 装配。"""

from fastapi import APIRouter

router = APIRouter(prefix="/v1/public")
