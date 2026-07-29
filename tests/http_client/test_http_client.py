from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from pkg.http_client import AsyncHttpClient, RequestResult


class TestRequestResult:
    """测试 RequestResult 数据类"""

    def test_success_property_with_2xx_status(self):
        """测试 2xx 状态码被识别为成功"""
        result = RequestResult(status_code=200, error=None)
        assert result.success is True

        result = RequestResult(status_code=201, error=None)
        assert result.success is True

        result = RequestResult(status_code=299, error=None)
        assert result.success is True

    def test_success_property_with_error(self):
        """测试有错误信息时不被识别为成功"""
        result = RequestResult(status_code=200, error="Some error")
        assert result.success is False

    def test_success_property_with_4xx_5xx_status(self):
        """测试 4xx/5xx 状态码不被识别为成功"""
        result = RequestResult(status_code=400, error="Bad Request")
        assert result.success is False

        result = RequestResult(status_code=500, error="Internal Server Error")
        assert result.success is False

    def test_json_caching(self):
        """测试 JSON 缓存机制"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"key": "value"}

        result = RequestResult(response=mock_response)

        # 第一次调用应该解析 JSON
        data1 = result.json()
        assert data1 == {"key": "value"}
        assert mock_response.json.call_count == 1

        # 第二次调用应该返回缓存，不再调用 response.json()
        data2 = result.json()
        assert data2 == {"key": "value"}
        assert mock_response.json.call_count == 1  # 仍然是 1

    def test_json_no_response(self):
        """测试没有响应时返回空字典"""
        result = RequestResult(response=None)
        assert result.json() == {}

    def test_json_parse_error(self):
        """测试 JSON 解析失败时抛出异常"""
        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError("Invalid JSON")

        result = RequestResult(response=mock_response)

        with pytest.raises(RuntimeError, match="Failed to parse JSON"):
            result.json()


class TestAsyncHttpClient:
    """测试 AsyncHttpClient 类"""

    @pytest_asyncio.fixture
    async def client(self):
        """创建测试客户端"""
        client = AsyncHttpClient(base_url="https://api.example.com", timeout=30)
        yield client
        await client.close()

    async def test_client_initialization(self):
        """测试客户端初始化"""
        client = AsyncHttpClient(
            base_url="https://test.com",
            timeout=60,
            headers={"X-Custom": "header"},
            verify=False,
        )

        assert client.timeout == 60
        assert client.default_headers["X-Custom"] == "header"
        assert client.client.base_url == "https://test.com"

        await client.close()

    async def test_context_manager(self):
        """测试上下文管理器"""
        async with AsyncHttpClient(base_url="https://test.com") as client:
            assert client is not None
            assert isinstance(client, AsyncHttpClient)

    async def test_get_error_message_success(self):
        """测试获取错误消息（成功场景）"""
        mock_response = MagicMock()
        mock_response.text = "Error message"
        mock_response.status_code = 400

        msg = AsyncHttpClient._get_error_message(mock_response)
        assert msg == "Error message"

    async def test_get_error_message_failure(self):
        """测试获取错误消息（失败场景）"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        # 设置 text 属性访问时抛出异常
        type(mock_response).text = property(lambda self: (_ for _ in ()).throw(Exception("Text error")))

        msg = AsyncHttpClient._get_error_message(mock_response)
        assert "Failed to get response.text" in msg
        assert "status_code=500" in msg

    @pytest.mark.parametrize("method,expected_method", [
        ("get", "GET"),
        ("post", "POST"),
        ("put", "PUT"),
        ("delete", "DELETE"),
    ])
    @pytest.mark.asyncio
    async def test_http_methods(self, client, method, expected_method):
        """测试 HTTP 方法调用"""
        with patch.object(client.client, "request", new_callable=AsyncMock) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.is_error = False
            mock_request.return_value = mock_response

            method_func = getattr(client, method)
            result = await method_func("/test", params={"key": "value"})

            assert result.status_code == 200
            assert result.success is True
            mock_request.assert_called_once()
            call_kwargs = mock_request.call_args.kwargs
            assert call_kwargs["method"] == expected_method

    @pytest.mark.asyncio
    async def test_request_with_error_response(self, client):
        """测试请求返回错误响应"""
        with patch.object(client.client, "request", new_callable=AsyncMock) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.is_error = True
            mock_response.text = "Not Found"
            mock_request.return_value = mock_response

            result = await client.get("/not-found")

            assert result.status_code == 404
            assert result.success is False
            assert result.error == "Not Found"

    @pytest.mark.asyncio
    async def test_request_with_http_status_error(self, client):
        """测试请求抛出 HTTPStatusError"""
        with patch.object(client.client, "request", new_callable=AsyncMock) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 500
            exc = httpx.HTTPStatusError("Server Error", request=MagicMock(), response=mock_response)
            mock_request.side_effect = exc

            result = await client.get("/error")

            assert result.status_code == 500
            assert "HTTPStatusError" in result.error

    @pytest.mark.asyncio
    async def test_request_with_request_error(self, client):
        """测试请求抛出 RequestError（网络错误）"""
        with patch.object(client.client, "request", new_callable=AsyncMock) as mock_request:
            mock_request.side_effect = httpx.RequestError("Connection failed")

            result = await client.get("/timeout")

            assert result.status_code == 0
            assert "RequestError" in result.error

    @pytest.mark.asyncio
    async def test_download_file_success(self, client, tmp_path):
        """测试文件下载成功"""
        save_path = tmp_path / "subdir" / "test.txt"
        file_content = b"Hello, World!" * 1000

        with patch.object(client, "_stream_context") as mock_stream:
            mock_response = MagicMock()
            mock_response.headers.get.return_value = str(len(file_content))
            mock_response.is_error = False

            async def mock_aiter_bytes(chunk_size):
                for i in range(0, len(file_content), chunk_size):
                    yield file_content[i:i + chunk_size]

            mock_response.aiter_bytes = mock_aiter_bytes

            mock_stream.return_value.__aenter__.return_value = mock_response

            progress_calls = []

            def on_progress(downloaded, total):
                progress_calls.append((downloaded, total))

            success, error = await client.download_file(
                url="/file.txt",
                save_path=str(save_path),
                chunk_size=1024,
                on_progress=on_progress,
            )

            assert success is True
            assert error == ""
            assert save_path.exists()
            assert save_path.read_bytes() == file_content
            assert len(progress_calls) > 0
            assert progress_calls[-1][0] == len(file_content)

    @pytest.mark.asyncio
    async def test_download_file_with_error_response(self, client, tmp_path):
        """测试文件下载遇到错误响应"""
        save_path = tmp_path / "test.txt"

        with patch.object(client, "_stream_context") as mock_stream:
            # 模拟 _stream_context 抛出 RuntimeError（因为 raise_for_status 会导致 HTTPStatusError，然后被转换为 RuntimeError）
            mock_stream.return_value.__aenter__.side_effect = RuntimeError(
                "Stream HTTPStatusError, status_code=404, error=404 Not Found"
            )

            success, error = await client.download_file(
                url="/not-found.txt",
                save_path=str(save_path),
            )

            assert success is False
            assert "404" in error
            assert not save_path.exists()

    @pytest.mark.asyncio
    async def test_stream_request(self, client):
        """测试流式请求"""
        with patch.object(client, "_stream_context") as mock_stream:
            mock_response = MagicMock()

            async def mock_aiter_bytes(chunk_size):
                for chunk in [b"chunk1", b"chunk2", b"chunk3"]:
                    yield chunk

            mock_response.aiter_bytes = mock_aiter_bytes
            mock_stream.return_value.__aenter__.return_value = mock_response

            chunks = []
            async for chunk in client.stream_request("GET", "/stream"):
                chunks.append(chunk)

            assert chunks == [b"chunk1", b"chunk2", b"chunk3"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
