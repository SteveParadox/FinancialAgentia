"""Bounded Ollama native chat transport using the existing HTTPX dependency.

The pinned legacy LangChain adapter ignored max_tokens and client_kwargs, so
could not enforce output bounds or cloud authentication. See docs.ollama.com/api/chat.
Shared LLM code exclusively owns retries and deadlines.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from urllib.parse import urlparse
import httpx

from ..utils._utils import LLMError, LLMTimeoutError, LLMConnectionError, LLMRateLimitError


class OllamaChatClient:
    def __init__(self, model: str, base_url: str, streaming: bool = False,
                 client_kwargs: dict | None = None, *, timeout: float = 60,
                 http_client: httpx.AsyncClient | None = None):
        parsed = urlparse(base_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Invalid Ollama base URL')
        headers = (client_kwargs or {}).get('headers', {})
        if headers and (parsed.scheme != 'https' or parsed.hostname != 'ollama.com'):
            raise ValueError('Ollama cloud credentials require https://ollama.com')
        self.model = model
        self.url = base_url.rstrip('/')+'/api/chat'
        self.headers = dict(headers)
        self.http = http_client or httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False,
            proxy=(os.getenv('HTTPS_PROXY') or os.getenv('https_proxy')) if parsed.scheme == 'https' else None,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=3))

    async def aclose(self):
        await self.http.aclose()

    def _request(self, messages, options, stream):
        limit = options.pop('max_tokens', 4096)
        temperature = options.pop('temperature', None)
        if type(limit) is not int or not 1 <= limit <= 32768 or options:
            raise ValueError('Invalid Ollama research options')
        converted = []
        for message in messages:
            role = message.get('role') if isinstance(message, dict) else {'human': 'user', 'ai': 'assistant', 'system': 'system'}.get(getattr(message, 'type', ''))
            content = message.get('content') if isinstance(message, dict) else getattr(message, 'content', None)
            if role not in {'user', 'assistant', 'system'} or not isinstance(content, str):
                raise ValueError('Ollama research messages must be typed text')
            converted.append({'role': role, 'content': content})
        return {'model': self.model, 'messages': converted, 'stream': stream,
                'options': {'num_predict': limit, **({'temperature': temperature} if temperature is not None else {})}}

    @staticmethod
    def _status(response):
        if response.status_code == 429:
            raise LLMRateLimitError('Ollama rate limited the request')
        if response.status_code >= 500:
            raise LLMConnectionError('Ollama temporarily unavailable')
        if response.status_code != 200:
            raise LLMError(f'Ollama rejected the request (HTTP {response.status_code})')

    @staticmethod
    def _frame(frame):
        if not isinstance(frame, dict) or frame.get('error') or not isinstance(frame.get('done'), bool):
            raise LLMError('Invalid Ollama response')
        content = frame.get('message', {}).get('content', '') if isinstance(frame.get('message'), dict) else None
        if not isinstance(content, str) or frame.get('message', {}).get('tool_calls'):
            raise LLMError('Unexpected Ollama tool or message response')
        if frame.get('done') and frame.get('done_reason') not in (None, 'stop'):
            raise LLMError('Ollama output did not finish normally')
        return content

    async def agenerate(self, batches, **options):
        if len(batches) != 1:
            raise ValueError('Exactly one Ollama completion per call')
        try:
            response = await self.http.post(self.url, headers=self.headers, json=self._request(batches[0], options, False))
            self._status(response)
            if len(response.content) > 1_000_000:
                raise LLMError('Ollama completion exceeded size limit')
            frame = response.json()
            content = self._frame(frame)
            if frame.get('done') is not True or not content.strip():
                raise LLMError('Ollama completion missing terminal content')
            usage = SimpleNamespace(input_tokens=frame['prompt_eval_count'], output_tokens=frame['eval_count']) if all(type(frame.get(key)) is int for key in ('prompt_eval_count', 'eval_count')) else None
            return SimpleNamespace(content=content, usage=usage)
        except httpx.TimeoutException:
            raise LLMTimeoutError('Ollama request timed out') from None
        except httpx.TransportError:
            raise LLMConnectionError('Ollama connection unavailable') from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise LLMError('Malformed Ollama completion') from None

    async def astream(self, messages, **options):
        try:
            async with self.http.stream('POST', self.url, headers=self.headers, json=self._request(messages, options, True)) as response:
                self._status(response)
                seen = 0; characters = 0; terminal = False
                # Bounded byte framing prevents a malicious/incomplete NDJSON
                # line from growing without a limit inside aiter_lines().
                buffer = b''
                async for chunk in response.aiter_bytes():
                    buffer += chunk
                    if len(buffer) > 1_000_000:
                        raise LLMError('Ollama stream frame exceeded limit')
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        if not line.strip():
                            continue
                        frame = json.loads(line)
                        content = self._frame(frame)
                        characters += len(content)
                        if characters > 100000:
                            raise LLMError('Ollama output exceeded size limit')
                        if content:
                            seen += 1
                            yield SimpleNamespace(content=content)
                        if frame.get('done'):
                            terminal = True
                            break
                    if terminal:
                        break
                if not terminal or not seen:
                    raise LLMError('Ollama stream ended without terminal content')
        except httpx.TimeoutException:
            raise LLMTimeoutError('Ollama stream timed out') from None
        except httpx.TransportError:
            raise LLMConnectionError('Ollama connection unavailable') from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise LLMError('Malformed Ollama stream') from None
