from django.conf import settings
from django.db import models


class Conversation(models.Model):
    """One chat thread. The active thread is tracked in the login session, so it
    persists across pages but every new login opens on a fresh chat; past
    threads stay reachable from the widget's chat list."""
    TITLE_SOURCE_CHOICES = [
        ('', 'Untitled'),
        ('question', 'First question'),  # placeholder until the AI names it
        ('ai', 'AI generated'),
        ('user', 'Renamed by user'),     # never overwritten by the AI
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='assistant_conversations')
    title = models.CharField(max_length=80, blank=True, default='')
    title_source = models.CharField(max_length=8, blank=True, default='',
                                    choices=TITLE_SOURCE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'Conversation {self.pk} ({self.user.username})'


class Message(models.Model):
    ROLE_CHOICES = [('user', 'User'), ('model', 'Assistant')]
    RATING_CHOICES = [(1, 'Thumbs up'), (-1, 'Thumbs down'), (0, 'No rating')]
    # What went wrong, picked from chips under a thumbs-down. Each points at a
    # different fix, which is the point of asking.
    REASON_CHOICES = [
        ('', 'Not given'),
        ('wrong', 'Wrong information'),
        ('unanswered', "Didn't answer my question"),
        ('outdated', 'Out of date'),
        ('other', 'Other'),
    ]

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE,
                                     related_name='messages')
    role = models.CharField(max_length=8, choices=ROLE_CHOICES)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    # Observability (assistant messages only)
    model_used = models.CharField(max_length=64, blank=True, default='')
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    feedback_sent = models.BooleanField(default=False)
    error = models.CharField(max_length=200, blank=True, default='')
    # Knowledge chunks retrieved for this reply, comma-separated. Tells a bad
    # answer's cause apart: the right chunk missing means retrieval, the right
    # chunk present means the chunk text or the prompt.
    knowledge_used = models.CharField(max_length=300, blank=True, default='')

    # Per-message quality feedback from the user
    rating = models.SmallIntegerField(choices=RATING_CHOICES, default=0)
    rating_reason = models.CharField(max_length=12, blank=True, default='',
                                     choices=REASON_CHOICES)
    rating_comment = models.TextField(blank=True, default='')
    # When the rating was last set, so the weekly digest catches an old reply
    # rated this week.
    rated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'[{self.role}] {self.text[:60]}'
