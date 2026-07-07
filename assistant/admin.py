from django.contrib import admin

from .models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ('role', 'text', 'created_at', 'model_used', 'latency_ms',
                       'feedback_sent', 'error', 'rating')
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'created_at', 'updated_at', 'message_count', 'thumbs_down')
    list_filter = ('created_at',)
    search_fields = ('user__username',)
    inlines = [MessageInline]

    def message_count(self, obj):
        return obj.messages.count()

    def thumbs_down(self, obj):
        return obj.messages.filter(rating=-1).count()


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'conversation', 'role', 'short_text', 'model_used',
                    'latency_ms', 'rating', 'error', 'created_at')
    list_filter = ('role', 'rating', 'model_used', 'feedback_sent')
    search_fields = ('text', 'conversation__user__username')

    def short_text(self, obj):
        return obj.text[:80]
