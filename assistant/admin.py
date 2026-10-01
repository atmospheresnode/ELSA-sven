from django.contrib import admin

from .models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ('role', 'text', 'created_at', 'model_used', 'latency_ms',
                       'feedback_sent', 'error', 'knowledge_used', 'rating',
                       'rating_reason', 'rating_comment', 'rated_at')
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'title', 'title_source', 'created_at', 'updated_at',
                    'message_count', 'thumbs_down')
    list_filter = ('created_at', 'title_source')
    search_fields = ('user__username', 'title')
    inlines = [MessageInline]

    def message_count(self, obj):
        return obj.messages.count()

    def thumbs_down(self, obj):
        return obj.messages.filter(rating=-1).count()


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'conversation', 'role', 'short_text', 'model_used',
                    'latency_ms', 'rating', 'rating_reason', 'knowledge_used',
                    'error', 'created_at')
    list_filter = ('role', 'rating', 'rating_reason', 'model_used', 'feedback_sent')
    # knowledge_used is searchable so "every answer built from alias.md" is one query
    search_fields = ('text', 'rating_comment', 'knowledge_used', 'conversation__user__username')

    def short_text(self, obj):
        return obj.text[:80]
