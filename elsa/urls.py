"""                                                                               ELSA URL Configuration

The `urlpatterns` list maps URLs to views. For more information please see:
    https://docs.djangoproject.com/en/1.11/topics/http/urls/

@Atmos Student
For those unfamiliar with the terminology function and class in regards to the programming language,
please see ksweebe/Documents/Learn/Python/functions_and_classes.txt 


Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  url(r'^$', views.home, name='home')


Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  url(r'^$', Home.as_view(), name='home')


Including another URLconf
    1. Import the include() function: from django.conf.urls import url, include
    2. Add a URL to urlpatterns:  url(r'^blog/', include('blog.urls'))


"""
from django.conf import settings
from django.conf.urls import url, include
from django.conf.urls.static import static
from django.contrib import admin
from main import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    url(r'^admin/', admin.site.urls),
    url(r'^', include('main.urls')),
    url(r'^accounts/', include('friends.urls')),
    url(r'^student_lair/', include('blog.urls')),
    url(r'build/', include('build.urls')),
    url(r'^review/', include('review.urls')),
    url(r'^tutorial/', include('tutorial.urls')),
    url('^', include('django.contrib.auth.urls')),
    #url(r'^build_a_bundle/(?P<pk_bundle>\d+)/contextquery/exist/', include('crawl_starbase.urls')),
]

# urlpatterns += [
#     url('captcha/', include('captcha.urls')),
#     url(r'^select2/', include('django_select2.urls')),
# ]


urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_DIR)
urlpatterns += static(settings.ARCHIVE_URL, document_root=settings.ARCHIVE_DIR)
