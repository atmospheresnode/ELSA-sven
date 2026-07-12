"""Regression evals for the ELSA Assistant.

Runs the golden questions in assistant/evals.json against the live model with
the real prompt pipeline, and checks each answer contains its required phrases.
Run this after changing the system prompt, knowledge base, or model chain.

NOTE: every question consumes free-tier quota (~20 requests/day/model), so run
with --limit while iterating.

Usage:
    python3 manage.py assistant_eval [--username NAME] [--limit N]
"""
import json
import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from assistant.llm import GeminiClient, LLMUnavailable, QuotaExhausted
from assistant.prompts import build_system_prompt

EVALS_PATH = Path(__file__).resolve().parents[2] / 'evals.json'


class Command(BaseCommand):
    help = 'Run the assistant golden-question evals against the live model.'

    def add_arguments(self, parser):
        parser.add_argument('--username', help='User whose context to build (default: first user)')
        parser.add_argument('--limit', type=int, default=0, help='Run only the first N questions')

    def handle(self, *args, **options):
        api_key = getattr(settings, 'GEMINI_API_KEY', '')
        if not api_key:
            raise CommandError('GEMINI_API_KEY is not configured.')

        if options['username']:
            user = User.objects.filter(username=options['username']).first()
            if user is None:
                raise CommandError(f"User {options['username']} not found.")
        else:
            user = User.objects.first()

        evals = json.loads(EVALS_PATH.read_text())
        if options['limit']:
            evals = evals[:options['limit']]

        self.stdout.write(f'Running {len(evals)} evals as user "{user.username}" '
                          f'(consumes ~{len(evals)} quota requests)...\n')

        client = GeminiClient(api_key)
        passed = 0
        for i, case in enumerate(evals, 1):
            question = case['question']
            # A case may carry prior turns ("history": [user, model, ...]) so
            # follow-up behavior is testable; retrieval sees the previous user
            # turn, mirroring the chat view.
            history = case.get('history', [])
            contents = []
            for turn_i, turn in enumerate(history):
                contents.append({'role': 'user' if turn_i % 2 == 0 else 'model',
                                 'parts': [{'text': turn}]})
            contents.append({'role': 'user', 'parts': [{'text': question}]})
            prev_user = history[-2] if len(history) >= 2 else (history[0] if history else '')
            retrieval_query = f'{prev_user[:300]} {question}' if prev_user else question
            prompt = build_system_prompt(user, query=retrieval_query)
            try:
                model, upstream = client.open_stream(prompt, contents)
                answer = ''.join(e.get('text', '') for e in client.iter_events(upstream))
            except (QuotaExhausted, LLMUnavailable) as exc:
                raise CommandError(f'Model unavailable at question {i}: {type(exc).__name__}')

            # must_include: required substring, or a list of alternatives of
            # which at least one must appear. must_not_include: forbidden
            # substrings (regression guard against invented features/claims).
            missing = []
            for phrase in case.get('must_include', []):
                options = phrase if isinstance(phrase, list) else [phrase]
                if not any(o.lower() in answer.lower() for o in options):
                    missing.append(phrase)
            forbidden = [phrase for phrase in case.get('must_not_include', [])
                         if phrase.lower() in answer.lower()]
            if missing or forbidden:
                self.stdout.write(self.style.ERROR(f'FAIL [{i}] {question}'))
                if missing:
                    self.stdout.write(f'     missing: {missing}')
                if forbidden:
                    self.stdout.write(f'     forbidden: {forbidden}')
                self.stdout.write(f'     answer:  {answer[:200]}...')
            else:
                passed += 1
                self.stdout.write(self.style.SUCCESS(f'PASS [{i}] {question}  ({model})'))
            time.sleep(1)  # stay under per-minute limits

        self.stdout.write(f'\n{passed}/{len(evals)} passed.')
        if passed < len(evals):
            raise CommandError('Some evals failed.')
