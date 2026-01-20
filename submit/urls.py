from django.urls import path
from . import views

app_name = 'submit'

urlpatterns = [
    path('', views.submit_main, name='submit_main'),
    path('upload/archive/', views.upload_archive, name='upload_archive'),
    path('upload/external/', views.upload_external, name='upload_external'),
]