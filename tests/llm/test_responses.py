import json
from contextlib import aclosing
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from pydantic import BaseModel

from pkg.llm import (
    OpenAIChatCompletionsClient,
    OpenAIResponsesClient,
    ResponseFailedError,
    ResponseIncompleteError,
    StructuredOutputParseError,
    StructuredOutputRefusalError,
)


class Answer(BaseModel):
    answer: str
    args: dict[str, int]


def response_body(
    *, text='{"answer":"ok","args":{"count":2}}', status="completed", output=None
):
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "model": "test-model",
        "status": status,
        "error": None,
        "incomplete_details": {"reason": "max_output_tokens"}
        if status == "incomplete"
        else None,
        "output": output
        if output is not None
        else [
            {
                "type": "message",
                "id": "msg_test",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,format_type",
    [("openai", "json_schema"), ("deepseek", "json_schema"), ("mimo", "json_object")],
)
async def test_structured_request_uses_responses_wire_contract(provider, format_type):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response_body())

    async with openai.AsyncOpenAI(
        api_key="test",
        base_url="https://example.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        client = OpenAIResponsesClient(
            base_url="https://example.test/v1",
            model="test-model",
            provider=provider,
            client=sdk,
        )
        observed = []
        result = await client.response_structured(
            input=[{"role": "user", "content": "answer"}],
            instructions="Be concise",
            response_model=Answer,
            max_output_tokens=100,
            temperature=0,
            reasoning={"effort": "none"},
            _audit_hook=observed.append,
        )
    assert result.args == {"count": 2}
    assert requests[0].url.path == "/v1/responses"
    body = json.loads(requests[0].content)
    assert body["max_output_tokens"] == 100
    assert body["reasoning"] == {"effort": "none"}
    assert body["stream"] is False and body["store"] is False
    assert body["text"]["format"]["type"] == format_type
    assert not {"messages", "max_tokens", "response_format", "thinking"}.intersection(
        body
    )
    if format_type == "json_object":
        assert (
            "JSON schema" in body["instructions"] and '"args"' in body["instructions"]
        )
    else:
        assert body["text"]["format"]["schema"]["properties"]["args"][
            "additionalProperties"
        ] == {"type": "integer"}
        assert body["text"]["format"]["strict"] is False
    assert observed[0].usage.input_tokens == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,error",
    [
        (response_body(status="incomplete"), ResponseIncompleteError),
        (response_body(status="failed"), ResponseFailedError),
        (response_body(text="not json"), StructuredOutputParseError),
        (response_body(text='{"answer":1}'), StructuredOutputParseError),
        (response_body(output=[]), StructuredOutputParseError),
        (
            response_body(
                output=[
                    {
                        "type": "message",
                        "id": "msg",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": "declined"}],
                    }
                ]
            ),
            StructuredOutputRefusalError,
        ),
    ],
)
async def test_invalid_structured_results_are_audited_before_rejection(payload, error):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        client = OpenAIResponsesClient(
            base_url="https://example.test", model="test", client=sdk
        )
        observed = []
        with pytest.raises(error):
            await client.response_structured(
                input="answer", response_model=Answer, _audit_hook=observed.append
            )
    assert len(requests) == 1
    assert len(observed) == 1


@pytest.mark.asyncio
async def test_response_preserves_function_calls_and_continuation_items():
    items = [{"type": "function_call_output", "call_id": "call_1", "output": "ok"}]
    tool = {"type": "function", "name": "lookup", "parameters": {"type": "object"}}
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=response_body(
                output=[
                    {
                        "type": "function_call",
                        "id": "fc",
                        "call_id": "call_2",
                        "name": "lookup",
                        "arguments": "{}",
                    }
                ]
            ),
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        client = OpenAIResponsesClient(
            base_url="https://example.test", model="test", client=sdk
        )
        result = await client.response(
            input=items, tools=[tool], previous_response_id="resp_previous", store=True
        )
    assert seen[0]["input"] == items and seen[0]["tools"] == [tool]
    assert seen[0]["previous_response_id"] == "resp_previous"
    assert seen[0]["store"] is True
    assert result.output[0].call_id == "call_2"


class EventStream(httpx.AsyncByteStream):
    def __init__(self, events):
        self.events = events
        self.closed = False

    async def __aiter__(self):
        for event in self.events:
            yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "incomplete", "failed"])
async def test_stream_preserves_text_tool_and_terminal_events_and_closes(terminal):
    events = [
        {
            "type": "response.output_text.delta",
            "sequence_number": 0,
            "item_id": "msg",
            "output_index": 0,
            "content_index": 0,
            "delta": "hi",
            "logprobs": [],
        },
        {
            "type": "response.function_call_arguments.delta",
            "sequence_number": 1,
            "item_id": "fc",
            "output_index": 1,
            "delta": "{}",
        },
        {
            "type": f"response.{terminal}",
            "sequence_number": 2,
            "response": response_body(status=terminal),
        },
    ]
    stream = EventStream(events)

    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        client = OpenAIResponsesClient(
            base_url="https://example.test", model="test", client=sdk
        )
        received = [event async for event in client.response_stream(input="hi")]
    assert [event.type for event in received] == [event["type"] for event in events]
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_early", [True, False])
async def test_stream_closes_on_early_exit_or_missing_terminal(stop_early):
    stream = EventStream(
        [
            {
                "type": "response.created",
                "sequence_number": 0,
                "response": response_body(status="in_progress"),
            }
        ]
    )

    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        client = OpenAIResponsesClient(
            base_url="https://example.test", model="test", client=sdk
        )
        if stop_early:
            async with aclosing(client.response_stream(input="hi")) as events:
                await anext(events)
        else:
            with pytest.raises(ResponseIncompleteError):
                _ = [event async for event in client.response_stream(input="hi")]
        assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client_type", [OpenAIResponsesClient, OpenAIChatCompletionsClient]
)
async def test_client_closes_owned_sdk_but_not_injected_sdk(client_type):
    client = client_type(base_url="https://example.test", model="test")
    sdk = client.client
    async with client:
        pass
    assert sdk.is_closed()
    async with openai.AsyncOpenAI(api_key="test") as injected:
        async with client_type(
            base_url="https://example.test", model="test", client=injected
        ):
            pass
        assert not injected.is_closed()


@pytest.mark.asyncio
async def test_no_format_fallback_on_http_failure():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "unsupported schema",
                    "type": "invalid_request_error",
                }
            },
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        client = OpenAIResponsesClient(
            base_url="https://example.test", model="test", client=sdk
        )
        with pytest.raises(openai.BadRequestError):
            await client.response_structured(input="hi", response_model=Answer)
    assert count == 1


@pytest.mark.asyncio
async def test_rejects_chat_parameters_before_request():
    async with OpenAIResponsesClient(
        base_url="https://example.test", model="test"
    ) as client:
        client.client.responses.create = AsyncMock()
        with pytest.raises(ValueError, match="max_tokens"):
            await client.response(input="hi", max_tokens=100)
        client.client.responses.create.assert_not_called()


@pytest.mark.asyncio
async def test_stream_closes_on_task_cancellation():
    import asyncio

    started = asyncio.Event()

    class WaitingStream(EventStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b""

    stream = WaitingStream([])

    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        client = OpenAIResponsesClient(
            base_url="https://example.test", model="test", client=sdk
        )

        async def consume():
            return [event async for event in client.response_stream(input="hi")]

        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed
