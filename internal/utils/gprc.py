from pkg.toolkit.grpc import GrpcChannel
from pkg.lazy_proxy import lazy_proxy

_grpc_channel: GrpcChannel | None = None
_channels: list[GrpcChannel] = []


def init_grpc_channel(*, host: str, port: int):
    """
    初始化gRPC 通道
    """
    global _grpc_channel
    _grpc_channel = GrpcChannel(host, port)
    _channels.append(_grpc_channel)


async def close_all_grpc_channel():
    """
    关闭gRPC通道
    """
    for channel in _channels:
        await channel.close()


def _get_grpc_channel() -> GrpcChannel:
    """
    获取gRPC通道
    """
    if not _grpc_channel:
        raise RuntimeError("gRPC channel not initialized.")
    return _grpc_channel


grpc_channel = lazy_proxy(_get_grpc_channel)
"""
class UserGrpcClient:
    def __init__(self):
        # 直接使用
        self.channel = grpc_channel.get_channel()
        self.stub = user_pb2_grpc.UserServiceStub(self.channel)

    async def get_user(self, uid: int):
        # 调用远程 GetUser 方法
        request = user_pb2.GetUserRequest(id=user_id)

        # 可以在这里注入通用的 Metadata，比如 trace_id 或 token
        metadata = (("x-client-id", "fastapi-app"),)

        response = await self.stub.GetUser(
            request,
            timeout=settings.GRPC_TIMEOUT,
            metadata=metadata
        )

        # 将 Proto Message 转换为 Python Dict 或 Pydantic Model 返回，解耦 Proto
        return {
            "id": response.id,
            "username": response.username,
            "email": response.email
        }
"""
