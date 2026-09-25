from django.urls import path

from . import views

app_name = 'assistant'

urlpatterns = [
    path('chat/', views.chat, name='chat'),
    path('history/', views.history, name='history'),
    path('rate/', views.rate, name='rate'),
    path('conversations/', views.conversations, name='conversations'),
    path('conversations/new/', views.new_chat, name='new_chat'),
    path('conversations/rename/', views.rename_conversation, name='rename_conversation'),
    path('conversations/delete/', views.delete_conversation, name='delete_conversation'),
    path('conversations/title/', views.auto_title, name='auto_title'),
]
