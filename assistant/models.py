from django.conf import settings
from django.db import models


class Conversation(models.Model):
    """One chat thread. A user's active conversation persists across pages;
    the widget starts a new one when the user clicks the new-chat button."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='assistant_conversations')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'Conversation {self.pk} ({self.user.username})'


class Message(models.Model):
    ROLE_CHOICES = [('user', 'User'), ('model', 'Assistant')]
    RATING_CHOICES = [(1, 'Thumbs up'), (-1, 'Thumbs down'), (0, 'No rating')]

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

    # Per-message quality feedback from the user
    rating = models.SmallIntegerField(choices=RATING_CHOICES, default=0)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'[{self.role}] {self.text[:60]}'
