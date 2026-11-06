"""Provider wrapper for the LLM API.

The app only talks to `GeminiClient`. To add a provider, write a class with
`open_stream() -> (model_name, response)` and `iter_events(response)`.
"""
import json
import logging

import requests

logger = logging.getLogger('assistant')

API_BASE = 'https://generativelanguage.googleapis.com/v1beta/models'

# Fastest first: 2.5-flash answers in about 0.5s, 3.5-flash takes 5s+ on the free tier.
# Swap the first two (or set GEMINI_MODELS) to prefer 3.5.
DEFAULT_MODELS = [
    'gemini-2.5-flash',
    'gemini-3.5-flash',
    'gemini-2.5-flash-lite',
    'gemini-flash-lite-latest',
]

# Short read timeout so the chain moves on when the newest model stalls.
READ_TIMEOUTS = {'gemini-3.5-flash': 15}
DEFAULT_READ_TIMEOUT = 60

GENERATION_CONFIG = {'maxOutputTokens': 2048, 'temperature': 0.4}

# How long to skip a failed model. A 429 lasts longer than congestion or a timeout.
COOLDOWN_QUOTA = 15 * 60
COOLDOWN_UNREACHABLE = 3 * 60


class QuotaExhausted(Exception):
    """Every model in the chain returned 429 — free daily buckets are empty."""


class LLMUnavailable(Exception):
    """No model in the chain could be reached or returned a usable response."""


class GeminiClient:

    def __init__(self, api_key, models=None):
        self.api_key = api_key
        self.models = models or DEFAULT_MODELS

    def open_stream_events(self, system_prompt, contents, tools=None):
        """Try each model in the chain and report progress.

        Yields ('trying', model, None) before each attempt and ends with ('connected', model, response).
        A 429, 5xx or network error moves on to the next model. Raises QuotaExhausted or LLMUnavailable
        if none connect. Failed models go on a shared cooldown so we don't re-probe them every message.
        """
        from django.core.cache import cache

        body = {
            'system_instruction': {'parts': [{'text': system_prompt}]},
            'contents': contents,
            'generationConfig': GENERATION_CONFIG,
        }
        if tools:
            body['tools'] = [{'functionDeclarations': tools}]

        saw_429 = False
        for model in self.models:
            cooldown = cache.get(f'assistant-model-cooldown-{model}')
            if cooldown:
                logger.info('assistant: skipping %s (cooldown: %s)', model, cooldown)
                saw_429 = saw_429 or cooldown == 'quota'
                continue

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
                cache.set(f'assistant-model-cooldown-{model}', 'unreachable', COOLDOWN_UNREACHABLE)
                continue
            if resp.status_code == 200:
                return_value = ('connected', model, resp)
                yield return_value
                return
            if resp.status_code == 429:
                saw_429 = True
                cache.set(f'assistant-model-cooldown-{model}', 'quota', COOLDOWN_QUOTA)
            else:
                cache.set(f'assistant-model-cooldown-{model}', f'http{resp.status_code}', COOLDOWN_UNREACHABLE)
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
        """Parse a streaming response into events and close it when done.

        Yields {'text': str}, {'function_call': {...}} and {'finish_reason': str} when generation
        ends abnormally. Network errors propagate as RequestException.
        """
        # Gemini's SSE stream has no charset, so force UTF-8 or accents get mangled.
        resp.encoding = 'utf-8'
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith('data: '):
                    continue
                try:
                    chunk = json.loads(line[len('data: '):])
                except ValueError:
                    continue
                # A prompt-level block comes with no candidates, so report that instead of an empty response.
                block = chunk.get('promptFeedback', {}).get('blockReason')
                if block:
                    logger.warning('assistant: prompt blocked (%s)', block)
                    yield {'finish_reason': f'PROMPT_{block}'}
                    continue
                try:
                    candidate = chunk['candidates'][0]
                except (KeyError, IndexError):
                    continue
                for part in candidate.get('content', {}).get('parts', []):
                    if part.get('text'):
                        yield {'text': part['text']}
                    if part.get('functionCall'):
                        yield {'function_call': part['functionCall']}
                finish = candidate.get('finishReason')
                if finish and finish != 'STOP':
                    logger.warning('assistant: generation ended with finishReason=%s', finish)
                    yield {'finish_reason': finish}
        finally:
            resp.close()
