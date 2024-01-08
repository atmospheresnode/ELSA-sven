"""                                                                               ELSA re_path Configuration

The `urlpatterns` list maps URLs to views. For more information please see:
    https://docs.djangoproject.com/en/1.11/topics/http/urls/

@Atmos Student
For those unfamiliar with the terminology function and class in regards to the programming language,
please see ksweebe/Documents/Learn/Python/functions_and_classes.txt 


Function views
    1. Add an import:  from my_app import views
    2. Add a re_path to urlpatterns:  re_path(r'^$', views.home, name='home')


Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a re_path to urlpatterns:  re_path(r'^$', Home.as_view(), name='home')


Including another URLconf
    1. Import the include() function: from django.conf.urls import re_path, include
    2. Add a re_path to urlpatterns:  re_path(r'^blog/', include('blog.urls'))


"""
from django.conf import settings
from django.conf.urls import re_path, include
from django.conf.urls.static import static
from django.contrib import admin
from main import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    re_path(r'^admin/', admin.site.urls),
    re_path(r'^', include('main.urls')),
    re_path(r'^accounts/', include('friends.urls')),
    re_path(r'^student_lair/', include('blog.urls')),
    re_path(r'build/', include('build.urls')),
    re_path(r'^review/', include('review.urls')),
    re_path(r'^tutorial/', include('tutorial.urls')),
    re_path('^', include('django.contrib.auth.urls')),
    #re_path(r'^build_a_bundle/(?P<pk_bundle>\d+)/contextquery/exist/', include('crawl_starbase.urls')),
]

# urlpatterns += [
#     re_path('captcha/', include('captcha.urls')),
#     re_path(r'^select2/', include('django_select2.urls')),
# ]


urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_DIR)
urlpatterns += static(settings.ARCHIVE_URL, document_root=settings.ARCHIVE_DIR)
