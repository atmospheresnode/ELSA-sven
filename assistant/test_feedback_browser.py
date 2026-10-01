"""Real browser tests for rating the assistant's replies, driven with Playwright.

They cover what no server-side test can reach: the widget's thumbs, the
"What went wrong?" panel that follows a thumbs-down, the order its requests
land in, and what a failed send looks like. The model is faked at the HTTP
layer, so a whole chat (question, streamed reply, vote, reason) runs through
the real view.

Playwright is a development-only dependency (see build/test_ama_browser.py);
without it, or without a browser, every test here skips.

    python3 manage.py test assistant.test_feedback_browser --settings=assistant.test_settings
"""
import os
from unittest.mock import patch

# Playwright's sync API runs a greenlet loop that Django mistakes for an async
# context; this test-only flag lets the ORM run. It never applies to the site.
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', '1')

from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.test import override_settings

from .models import Conversation, Message
from .tests import FakeUpstream
from .views import ACTIVE_SESSION_KEY

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - dev-only dependency
    sync_playwright = None

RATE_URL = '**/assistant/rate/'


@override_settings(ALLOWED_HOSTS=['*'], GEMINI_API_KEY='test-key')
class FeedbackWidgetBrowserTests(StaticLiveServerTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.playwright = None
        cls.browser = None
        if sync_playwright is None:
            return
        try:
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment dependent
            cls.browser = None
            cls.launch_error = exc

    @classmethod
    def tearDownClass(cls):
        if cls.browser is not None:
            cls.browser.close()
        if cls.playwright is not None:
            cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        if self.browser is None:
            self.skipTest('Playwright browser unavailable: {}'.format(
                getattr(self, 'launch_error', 'playwright not installed')))
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(username='rater', password='pw-for-tests')
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.js_errors = []
        self.page.on('pageerror', lambda exc: self.js_errors.append(str(exc)))
        self.login()

    def tearDown(self):
        self.assertEqual(self.js_errors, [], 'the page threw JavaScript errors')
        super().tearDown()

    def login(self):
        """Install a session cookie directly; the login flow is not under test."""
        from django.conf import settings as django_settings
        from django.contrib.auth import login
        from django.contrib.sessions.backends.db import SessionStore
        from django.http import HttpRequest

        request = HttpRequest()
        request.session = SessionStore()
        self.user.backend = 'django.contrib.auth.backends.ModelBackend'
        login(request, self.user)
        request.session.save()
        self.session = request.session
        self.page.goto(self.live_server_url + '/static/css/styles.css')
        self.context.add_cookies([{
            'name': django_settings.SESSION_COOKIE_NAME,
            'value': request.session.session_key,
            'url': self.live_server_url,
        }])

    # -- helpers -------------------------------------------------------------

    def open_widget(self):
        self.page.goto(self.live_server_url + '/')
        # The widget sends ?page= so the server can name the bundle in view.
        with self.page.expect_response(lambda r: '/assistant/history/' in r.url):
            self.page.click('#elsaAssistantToggle')

    def ask(self, question, answer):
        """Send a question through the real chat view with the model faked."""
        with patch('assistant.llm.requests.post', return_value=FakeUpstream([answer])):
            self.page.fill('#elsaAssistantInput', question)
            with self.page.expect_response('**/assistant/chat/'):
                self.page.press('#elsaAssistantInput', 'Enter')
            # The rating bar is added when the stream's final event arrives
            self.page.wait_for_selector('.elsa-row.model .elsa-rate-bar')
        return Message.objects.filter(role='model').latest('created_at')

    def activate(self, conv):
        """Make `conv` the thread the widget opens: the active one lives in the
        login session, and a new login otherwise starts on a fresh chat."""
        self.session[ACTIVE_SESSION_KEY] = conv.pk
        self.session.save()

    def seed(self, rating=0):
        conv = Conversation.objects.create(user=self.user)
        conv.messages.create(role='user', text='Is the Alias required?')
        self.activate(conv)
        return conv.messages.create(role='model', text='It is optional.', rating=rating)

    def click_and_wait(self, selector):
        """Click something that posts a rating and wait for the server to answer."""
        with self.page.expect_response(RATE_URL) as info:
            self.page.click(selector)
        self.assertEqual(info.value.status, 200)

    def down(self):
        return '.elsa-row.model:last-child .elsa-rate-btn[aria-label="Bad answer"]'

    def up(self):
        return '.elsa-row.model:last-child .elsa-rate-btn[aria-label="Good answer"]'

    def chip(self, label):
        return f'.elsa-reason-chip:has-text("{label}")'

    def fresh(self, message):
        message.refresh_from_db()
        return message

    # -- tests ---------------------------------------------------------------

    def test_whole_loop_question_reply_thumbs_down_reason(self):
        self.open_widget()
        reply = self.ask('What goes in Citation Information?', 'Authors and a publication year.')
        self.assertIn('citation_information', reply.knowledge_used)
        self.assertEqual(self.page.locator('.elsa-reason').count(), 0)

        self.click_and_wait(self.down())
        reply = self.fresh(reply)
        self.assertEqual(reply.rating, -1)
        self.assertIsNotNone(reply.rated_at)
        self.assertTrue(self.page.is_visible('.elsa-reason'))
        self.assertIn('active', self.page.get_attribute(self.down(), 'class'))
        # Once the smooth scroll settles, the whole panel, Send button included, is in view
        self.page.wait_for_function(
            '() => { const box = document.querySelector(".elsa-reason").getBoundingClientRect(),'
            ' view = document.getElementById("elsaAssistantMessages").getBoundingClientRect();'
            ' return box.top >= view.top - 1 && box.bottom <= view.bottom + 1; }', timeout=3000)

        self.page.click(self.chip('Wrong info'))
        self.assertEqual(self.page.get_attribute(self.chip('Wrong info'), 'aria-pressed'), 'true')
        self.page.fill('.elsa-reason-comment', 'The editors are filled in automatically.')
        self.click_and_wait('.elsa-reason-send')

        self.page.wait_for_selector('.elsa-reason-thanks')
        self.assertEqual(self.page.locator('.elsa-reason').count(), 0)
        self.assertNotIn('\u2014', self.page.inner_text('.elsa-reason-thanks'))
        reply = self.fresh(reply)
        self.assertEqual((reply.rating, reply.rating_reason, reply.rating_comment),
                         (-1, 'wrong', 'The editors are filled in automatically.'))

    def test_switching_to_thumbs_up_clears_panel_and_reason(self):
        reply = self.seed()
        self.open_widget()
        self.click_and_wait(self.down())
        self.page.click(self.chip('Out of date'))
        self.click_and_wait('.elsa-reason-send')
        self.page.wait_for_selector('.elsa-reason-thanks')

        self.click_and_wait(self.up())
        self.assertEqual(self.page.locator('.elsa-reason, .elsa-reason-thanks').count(), 0)
        reply = self.fresh(reply)
        self.assertEqual((reply.rating, reply.rating_reason), (1, ''))
        self.assertIn('active', self.page.get_attribute(self.up(), 'class'))
        self.assertNotIn('active', self.page.get_attribute(self.down(), 'class'))

    def test_skip_keeps_the_vote(self):
        reply = self.seed()
        self.open_widget()
        self.click_and_wait(self.down())
        self.page.click('.elsa-reason-skip')
        self.assertEqual(self.page.locator('.elsa-reason').count(), 0)
        reply = self.fresh(reply)
        self.assertEqual((reply.rating, reply.rating_reason), (-1, ''))

    def test_send_with_nothing_chosen_just_closes(self):
        reply = self.seed()
        self.open_widget()
        self.click_and_wait(self.down())
        requests = []
        self.page.on('request', lambda r: requests.append(r.url) if '/assistant/rate/' in r.url else None)
        self.page.click(self.chip('Other'))
        self.page.click(self.chip('Other'))  # a second click deselects
        self.assertEqual(self.page.get_attribute(self.chip('Other'), 'aria-pressed'), 'false')
        self.page.click('.elsa-reason-send')
        self.assertEqual(self.page.locator('.elsa-reason').count(), 0)
        self.page.wait_for_timeout(300)
        self.assertEqual(requests, [])
        self.assertEqual(self.fresh(reply).rating, -1)

    def test_comment_alone_is_enough(self):
        reply = self.seed()
        self.open_widget()
        self.click_and_wait(self.down())
        self.page.fill('.elsa-reason-comment', '  needs a link to the page  ')
        self.click_and_wait('.elsa-reason-send')
        reply = self.fresh(reply)
        self.assertEqual((reply.rating_reason, reply.rating_comment), ('', 'needs a link to the page'))

    def test_failed_send_says_so_and_can_be_retried(self):
        reply = self.seed()
        self.open_widget()
        self.click_and_wait(self.down())
        self.page.click(self.chip("Didn't answer my question"))

        self.page.route(RATE_URL, lambda route: route.fulfill(status=500, body='{}'))
        self.page.click('.elsa-reason-send')
        self.page.wait_for_selector('.elsa-reason-error:has-text("Could not send")')
        self.assertFalse(self.page.is_disabled('.elsa-reason-send'))
        self.assertEqual(self.fresh(reply).rating_reason, '')

        self.page.unroute(RATE_URL)
        self.click_and_wait('.elsa-reason-send')
        self.page.wait_for_selector('.elsa-reason-thanks')
        self.assertEqual(self.fresh(reply).rating_reason, 'unanswered')

    def test_quick_vote_reason_undo_lands_in_order(self):
        """Thumbs-down, reason, undo in quick succession, with the server slow
        to finish the first request. Sent in parallel, the late bare vote would
        land last and leave a thumbs-down the user took back; queued, the undo
        is what sticks."""
        import threading
        import time as real_time
        from django.utils import timezone as real_timezone

        reply = self.seed()
        self.open_widget()
        calls = []
        lock = threading.Lock()

        class SlowFirstClock:
            """The rate view stamps rated_at with timezone.now(); the first
            stamp of the test stalls, so request one finishes after the rest."""
            @staticmethod
            def now():
                with lock:
                    calls.append(1)
                    first = len(calls) == 1
                if first:
                    real_time.sleep(0.8)
                return real_timezone.now()

        with patch('assistant.views.timezone', SlowFirstClock):
            self.page.click(self.down())
            self.page.click(self.chip('Wrong info'))
            self.page.click('.elsa-reason-send')
            self.page.click(self.down())  # undo before anything has answered
            deadline = real_time.monotonic() + 8
            while real_time.monotonic() < deadline:
                reply = self.fresh(reply)
                if len(calls) >= 2 and reply.rating == 0:
                    break
                self.page.wait_for_timeout(100)
            self.page.wait_for_timeout(1200)  # let any straggler land
        reply = self.fresh(reply)
        self.assertNotIn('active', self.page.get_attribute(self.down(), 'class'))
        self.assertEqual((reply.rating, reply.rating_reason, reply.rated_at), (0, '', None))

    def test_restored_thumbs_down_shows_no_panel_and_can_be_undone(self):
        reply = self.seed(rating=-1)
        self.open_widget()
        self.page.wait_for_selector(self.down())
        self.assertIn('active', self.page.get_attribute(self.down(), 'class'))
        self.assertEqual(self.page.locator('.elsa-reason').count(), 0)
        self.click_and_wait(self.down())
        self.assertEqual(self.page.locator('.elsa-reason').count(), 0)
        self.assertEqual(self.fresh(reply).rating, 0)

    def test_panel_on_an_earlier_reply_stays_with_that_reply(self):
        conv = Conversation.objects.create(user=self.user)
        first = None
        for i in range(6):
            conv.messages.create(role='user', text=f'question {i}')
            m = conv.messages.create(role='model', text=f'answer {i}\n\n' + 'filler line\n' * 8)
            first = first or m
        self.activate(conv)
        self.open_widget()
        first_down = '.elsa-row.model >> nth=0 >> .elsa-rate-btn[aria-label="Bad answer"]'
        self.page.locator(first_down).scroll_into_view_if_needed()
        with self.page.expect_response(RATE_URL):
            self.page.locator(first_down).click()
        panel = self.page.locator('.elsa-row.model >> nth=0 >> .elsa-reason')
        self.assertEqual(panel.count(), 1)
        self.assertTrue(panel.is_visible())
        self.assertTrue(panel.evaluate(
            'el => { const box = el.getBoundingClientRect(), view = '
            'document.getElementById("elsaAssistantMessages").getBoundingClientRect();'
            ' return box.top >= view.top - 1 && box.bottom <= view.bottom + 1; }'))
        self.assertEqual(self.fresh(first).rating, -1)

    def test_panel_fits_a_phone_screen(self):
        self.page.set_viewport_size({'width': 375, 'height': 740})
        self.seed()
        self.open_widget()
        self.click_and_wait(self.down())
        overflow = self.page.evaluate(
            '() => { const m = document.getElementById("elsaAssistantMessages");'
            ' return [m.scrollWidth - m.clientWidth, document.documentElement.scrollWidth - innerWidth]; }')
        self.assertEqual(overflow, [0, 0])

    def test_keyboard_only(self):
        """The panel's controls are real buttons and a labelled textarea."""
        self.seed()
        self.open_widget()
        self.page.focus(self.down())
        with self.page.expect_response(RATE_URL):
            self.page.keyboard.press('Enter')
        self.page.focus(self.chip('Out of date'))
        self.page.keyboard.press('Space')
        self.assertEqual(self.page.get_attribute(self.chip('Out of date'), 'aria-pressed'), 'true')
        self.assertEqual(self.page.get_attribute('.elsa-reason-comment', 'aria-label'), 'Tell us more')
