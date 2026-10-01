"""Usage and quality report for the assistant; closes the ratings loop.

    python3 manage.py assistant_stats             # last 7 days
    python3 manage.py assistant_stats --days 30
    python3 manage.py assistant_stats --email     # also email staff, if anything was thumbed down
    python3 manage.py assistant_stats --export-evals drafts.json

Prints volume, model mix, latency percentiles, error breakdown, ratings, and
the actual thumbs-down exchanges so they can be triaged into knowledge-chunk
updates or new eval cases. A thumbs-down counts in the window it was *rated*
in, so an old reply voted down this week still shows up.

--email is meant for a weekly cron (see assistant/README.md). It sends nothing
when there is no thumbs-down, so an empty week costs nobody an email.

--export-evals writes each thumbs-down question as a draft eval case, with
must_include left empty for a person to fill in. assistant_eval refuses a case
with nothing to check, so a draft copied into evals.json unfinished cannot pass
by accident. The drafts hold users' own words: reword anything personal before
committing them.
"""
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path

from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.timezone import localtime

from assistant.models import Message

EVALS_PATH = Path(__file__).resolve().parents[2] / 'evals.json'

STAFF_RECIPIENTS = ['lneakras@nmsu.edu', 'rupakdey@nmsu.edu']

# How many thumbs-down exchanges one report lists in full
MAX_LISTED = 50

# Where to look first, by the reason the user gave
TRIAGE_HINTS = {
    'wrong': 'Check the knowledge chunks it used: the fact is wrong there, or the model ignored it.',
    'unanswered': 'Retrieval probably missed: is the chunk that answers this in the list?',
    'outdated': 'A knowledge chunk has fallen behind ELSA: update it.',
}

# Pill label and colours per reason in the emailed digest
REASON_STYLES = {
    'wrong': {'label': 'Wrong information', 'bg': '#fef3f2', 'fg': '#b42318'},
    'unanswered': {'label': "Didn't answer", 'bg': '#fffaeb', 'fg': '#b54708'},
    'outdated': {'label': 'Out of date', 'bg': '#eff8ff', 'fg': '#175cd3'},
    'other': {'label': 'Other', 'bg': '#f4f3ff', 'fg': '#5925dc'},
    '': {'label': 'No reason given', 'bg': '#f2f4f7', 'fg': '#475467'},
}

# How many chunks the digest names as behind the most bad answers
TOP_CHUNKS = 5


def _percentile(sorted_values, pct):
    if not sorted_values:
        return 0
    idx = min(len(sorted_values) - 1, int(len(sorted_values) * pct / 100))
    return sorted_values[idx]


def _normalize(question):
    return ' '.join(question.lower().split())


def thumbs_down_since(since):
    """Thumbs-down replies rated since `since`. Ratings from before rated_at
    existed have no timestamp and fall back to when the reply was written."""
    return (Message.objects
            .filter(role='model', rating=-1)
            .filter(Q(rated_at__gte=since) | Q(rated_at__isnull=True, created_at__gte=since))
            .select_related('conversation__user')
            .order_by('-created_at'))


def exchange(reply):
    """The reply with the turns that led to it: the question it answered, and
    the exchange before that when the question was a follow-up."""
    earlier = list(reply.conversation.messages
                   .filter(created_at__lt=reply.created_at)
                   .order_by('-created_at', '-pk')[:3])
    question = earlier[0] if earlier and earlier[0].role == 'user' else None
    history = []
    if question and len(earlier) == 3 and [m.role for m in earlier[1:]] == ['model', 'user']:
        history = [earlier[2].text, earlier[1].text]
    return {
        'id': reply.pk,
        'conversation_id': reply.conversation_id,
        'username': reply.conversation.user.username,
        'when': localtime(reply.rated_at or reply.created_at).strftime('%Y-%m-%d %H:%M'),
        'rated': reply.rated_at or reply.created_at,
        'question': question.text if question else '',
        'history': history,
        'answer': reply.text,
        'reason': reply.get_rating_reason_display() if reply.rating_reason else '',
        'comment': reply.rating_comment,
        'knowledge_used': reply.knowledge_used.replace(',', ', ') or 'none recorded',
        'chunks': [name for name in reply.knowledge_used.split(',') if name],
        'hint': TRIAGE_HINTS.get(reply.rating_reason, ''),
        'style': REASON_STYLES.get(reply.rating_reason, REASON_STYLES['']),
    }



LOGO_CID = 'elsa_logo'


def _logo():
    """ELSA's inline email logo (shared with the sign-in emails), or None: a
    missing logo must never stop the digest going out."""
    try:
        from friends.views import _elsa_logo_attachment
        return _elsa_logo_attachment()
    except Exception:
        return None


def digest_context(rows, down_qs, since, replies, ups):
    """Everything the digest templates show. Counts cover every thumbs-down in
    the window, not only the rows listed in full."""
    total = down_qs.count()
    by_reason = Counter(down_qs.values_list('rating_reason', flat=True))
    reasons = [dict(REASON_STYLES[key], count=by_reason[key])
               for key in REASON_STYLES if by_reason.get(key)]
    chunk_counts = Counter(name for used in down_qs.values_list('knowledge_used', flat=True)
                           for name in used.split(',') if name)
    start, end = localtime(since), localtime(timezone.now())
    return {
        'total': total,
        'ups': ups,
        'replies': replies,
        'period': f'{start:%b} {start.day} to {end:%b} {end.day}, {end.year}',
        'preheader': ', '.join(f"{r['count']} {r['label'].lower()}" for r in reasons),
        'reasons': reasons,
        'top_chunks': [{'name': name, 'count': count}
                       for name, count in chunk_counts.most_common(TOP_CHUNKS)],
        'rows': rows,
        'more': total - len(rows),
    }

class Command(BaseCommand):
    help = 'Report assistant usage, latency, errors, and rated conversations.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--email', action='store_true',
                            help='Email the thumbs-down exchanges to staff (only if there are any).')
        parser.add_argument('--export-evals', metavar='PATH',
                            help='Write the thumbs-down questions to PATH as draft eval cases.')

    def handle(self, *args, **options):
        days = options['days']
        since = timezone.now() - timedelta(days=days)
        replies = Message.objects.filter(role='model', created_at__gte=since)
        users = (Message.objects.filter(role='user', created_at__gte=since)
                 .values('conversation__user').distinct().count())

        self.stdout.write(f'=== Assistant stats, last {days} day(s) ===')
        self.stdout.write(f'Replies: {replies.count()}  |  Active users: {users}')

        self.stdout.write('\nModel mix:')
        for row in (replies.exclude(model_used='').values('model_used')
                    .annotate(n=Count('id')).order_by('-n')):
            self.stdout.write(f"  {row['model_used']}: {row['n']}")

        latencies = sorted(replies.exclude(latency_ms=None)
                           .values_list('latency_ms', flat=True))
        if latencies:
            self.stdout.write(
                f'\nLatency ms: p50={_percentile(latencies, 50)} '
                f'p90={_percentile(latencies, 90)} p99={_percentile(latencies, 99)} '
                f'max={latencies[-1]}')

        errors = (replies.exclude(error='').values('error')
                  .annotate(n=Count('id')).order_by('-n'))
        self.stdout.write('\nErrors:' if errors else '\nErrors: none')
        for row in errors:
            self.stdout.write(f"  {row['error']}: {row['n']}")

        ups = replies.filter(rating=1).count()
        downs = replies.filter(rating=-1).count()
        self.stdout.write(f'\nRatings: {ups} up / {downs} down '
                          f'({replies.filter(rating=0).count()} unrated)')

        down_qs = thumbs_down_since(since)
        down_total = down_qs.count()
        down_rows = [exchange(reply) for reply in down_qs[:MAX_LISTED]]
        if down_rows:
            self.stdout.write(f'\n=== Thumbs-down exchanges rated in this window: '
                              f'{down_total} (triage these) ===')
        for row in down_rows:
            self.stdout.write(f"\n[message {row['id']}, conv {row['conversation_id']}, "
                              f"{row['username']}, {row['when']}]")
            if row['question']:
                self.stdout.write(f"  Q: {row['question'][:200]}")
            self.stdout.write(f"  A: {row['answer'][:300]}")
            if row['reason']:
                self.stdout.write(f"  Reason: {row['reason']}")
            if row['comment']:
                self.stdout.write(f"  Comment: {row['comment'][:300]}")
            self.stdout.write(f"  Knowledge used: {row['knowledge_used']}")
            if row['hint']:
                self.stdout.write(f"  Look at: {row['hint']}")
        if down_total > len(down_rows):
            self.stdout.write(f'\n({down_total - len(down_rows)} more in the admin: '
                              'Messages, filtered by rating = Thumbs down.)')

        if options['email']:
            if down_rows:
                self._email(down_rows, down_qs, since, replies.count(), ups)
                self.stdout.write(self.style.SUCCESS(
                    f'\nEmailed {down_total} thumbs-down to {", ".join(STAFF_RECIPIENTS)}.'))
            else:
                self.stdout.write('\nNo thumbs-down in this window; no email sent.')

        if options['export_evals']:
            self._export_evals(Path(options['export_evals']), down_rows)

    def _email(self, rows, down_qs, since, replies, ups):
        context = digest_context(rows, down_qs, since, replies, ups)
        logo = _logo()
        context['logo_cid'] = LOGO_CID if logo else ''
        total = context['total']
        # Multipart like ELSA's other emails: clients that refuse HTML still get
        # a readable plain-text digest.
        email = EmailMultiAlternatives(
            subject=f'[ELSA Assistant] {total} thumbs-down answer{"" if total == 1 else "s"} to review',
            body=render_to_string('assistant/email/weekly_digest.txt', context),
            from_email='atm-elsa@nmsu.edu',
            to=STAFF_RECIPIENTS,
        )
        email.attach_alternative(render_to_string('assistant/email/weekly_digest.html', context),
                                 'text/html')
        if logo:
            email.mixed_subtype = 'related'
            email.attach(logo)
        email.send(fail_silently=False)

    def _export_evals(self, path, rows):
        if path.exists():
            raise CommandError(f'{path} already exists; choose a new path so nothing is overwritten.')
        known = {_normalize(case['question']) for case in json.loads(EVALS_PATH.read_text())}
        drafts, skipped = [], 0
        for row in rows:
            key = _normalize(row['question'])
            if not key or key in known:
                skipped += 1
                continue
            known.add(key)
            draft = {'question': row['question']}
            if row['history']:
                draft['history'] = row['history']
            draft.update({
                'must_include': [],
                'must_not_include': [],
                # Context for whoever finishes the draft; delete before committing.
                '_review': {
                    'message_id': row['id'],
                    'reason': row['reason'],
                    'comment': row['comment'],
                    'bad_answer': row['answer'][:1000],
                    'knowledge_used': row['knowledge_used'],
                },
            })
            drafts.append(draft)
        path.write_text(json.dumps(drafts, indent=2, ensure_ascii=False) + '\n')
        self.stdout.write(self.style.SUCCESS(
            f'\nWrote {len(drafts)} draft eval case(s) to {path}'
            + (f' ({skipped} skipped: already in evals.json, repeated, or no question).' if skipped else '.')))
        if drafts:
            self.stdout.write('Fill in must_include / must_not_include, delete each "_review", reword '
                              'anything personal, then add them to assistant/evals.json.')
