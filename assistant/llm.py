"""Thin provider abstraction over the LLM API.

The rest of the app talks to `GeminiClient` only, so swapping providers (or
running two side by side) later means adding a class with the same interface:
`open_stream() -> (model_name, response)` + `iter_events(response)`.
"""
import json
import logging

import requests

logger = logging.getLogger('assistant')

API_BASE = 'https://generativelanguage.googleapis.com/v1beta/models'

# Ordered fastest-first: 2.5-flash answers in ~0.5s while 3.5-flash takes ~5s+
# to first token on the free tier — for chat, perceived latency wins. Swap the
# first two entries (or set GEMINI_MODELS) to prefer 3.5's quality instead.
DEFAULT_MODELS = [
    'gemini-2.5-flash',
    'gemini-3.5-flash',
    'gemini-2.5-flash-lite',
    'gemini-flash-lite-latest',
    'gemini-2.0-flash',
]

# The newest model often stalls under free-tier congestion. A short read
# timeout makes the chain fall through in seconds instead of freezing the chat.
READ_TIMEOUTS = {'gemini-3.5-flash': 15}
DEFAULT_READ_TIMEOUT = 60

GENERATION_CONFIG = {'maxOutputTokens': 1024, 'temperature': 0.4}


class QuotaExhausted(Exception):
    """Every model in the chain returned 429 — free daily buckets are empty."""


class LLMUnavailable(Exception):
    """No model in the chain could be reached or returned a usable response."""


class GeminiClient:

    def __init__(self, api_key, models=None):
        self.api_key = api_key
        self.models = models or DEFAULT_MODELS

    def open_stream_events(self, system_prompt, contents, tools=None):
        """Try each model in the chain, reporting progress as it goes.

        Yields ('trying', model, None) before each attempt and ends with
        ('connected', model, streaming_response). A 429 means that model's own
        free-tier bucket is exhausted; 5xx or a network error means it's
        overloaded — either way the next model may still work. Raises
        QuotaExhausted / LLMUnavailable when the chain ends without a connection.
        """
        body = {
            'system_instruction': {'parts': [{'text': system_prompt}]},
            'contents': contents,
            'generationConfig': GENERATION_CONFIG,
        }
        if tools:
            body['tools'] = [{'functionDeclarations': tools}]

        saw_429 = False
        for model in self.models:
            yield ('trying', model, None)
            try:
                resp = requests.post(
                    f'{API_BASE}/{model}:streamGenerateContent?alt=sse',
                    headers={'x-goog-api-key': self.api_key, 'Content-Type': 'application/json'},
                    json=body,
                    stream=True,
                    timeout=(10, READ_TIMEOUTS.get(model, DEFAULT_READ_TIMEOUT)),
                )
            except requests.RequestException as exc:
                logger.warning('assistant: %s unreachable (%s)', model, type(exc).__name__)
                continue
            if resp.status_code == 200:
                yield ('connected', model, resp)
                return
            saw_429 = saw_429 or resp.status_code == 429
            logger.warning('assistant: %s returned HTTP %s', model, resp.status_code)
            resp.close()

        if saw_429:
            raise QuotaExhausted()
        raise LLMUnavailable()

    def open_stream(self, system_prompt, contents, tools=None):
        """Blocking variant of open_stream_events: return (model, response)."""
        for kind, model, resp in self.open_stream_events(system_prompt, contents, tools):
            if kind == 'connected':
                return model, resp
        raise LLMUnavailable()  # unreachable: the generator raises first

    @staticmethod
    def iter_events(resp):
        """Parse a streaming response into events; closes the response when done.

        Yields {'text': str} for text deltas and {'function_call': {...}} for
        tool calls. Network errors mid-stream propagate as RequestException.
        """
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith('data: '):
                    continue
                try:
                    chunk = json.loads(line[len('data: '):])
                    parts = chunk['candidates'][0]['content']['parts']
                except (KeyError, IndexError, ValueError):
                    continue
                for part in parts:
                    if part.get('text'):
                        yield {'text': part['text']}
                    if part.get('functionCall'):
                        yield {'function_call': part['functionCall']}
        finally:
            resp.close()
