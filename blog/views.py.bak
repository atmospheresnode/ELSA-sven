# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.contrib.auth.decorators import user_passes_test, permission_required
from django.shortcuts import render, get_object_or_404
# Stdlib imports
import random

# Core Django imports
from django.utils import timezone

# Third-party app imports

# Imports from apps
from .forms import PostForm, CategoryForm
from .models import Post, Comment, Category
from main.models import Joke


# Secure page information here. ----------------
#     The following source talks about permission_required and the pass test decorator
#     source: https://docs.djangoproject.com/en/1.11/topics/auth/default/
#
#     The following source mentions a comment about group decorators.
#     source: https://bradmontgomery.net/blog/restricting-access-by-group-in-django/
#
#     The following source is a similar decorator definition and implementation.
#     source: https://gist.github.com/bradmontgomery/5657267
def atmos_student(function=None):
    actual_decorator = user_passes_test(
        lambda u: u.is_authenticated() and u.groups.filter(name='Atmos Student').exists()
    )
    return actual_decorator(function)
#
#
#  End secure page information here. ----------------


# Create your views here. ----------------
#
#
# index is simply the homepage for the blog app.
@atmos_student
def index(request):
    post_list = Post.objects.order_by('date')[:3]
    category_list = Category.objects.order_by('-views')[:5]
    random_index = random.randint(0, Joke.objects.count()-1)
    random_joke = Joke.objects.all()[random_index]
    context_dict = {'posts':post_list, 'categories':category_list, 'joke':random_joke}
    return render(request, 'blog/index.html', context_dict)


# archive is a listing of all blog posts within ELSA.
@atmos_student
def archive(request):    
    posts = Post.objects.order_by('date')
    form = PostForm()
    if request.method == 'POST':
       form = PostForm(request.POST)
       if form.is_valid():
           post = form.save(commit=False) # means we do not want to save the Post model yet.
           post.user = request.user
           post.date = timezone.now()
           post.save()                    # and now we save
           return render(request, 'blog/archive.html', {'posts':posts})
    else:
        form = PostForm()
    return render(request, 'blog/archive.html', {'posts':posts,
                                                 'form':form,
                                                })

# detail shows a detailed display of each post.  I really wanted to call this post but I didn't know if there would be naming convention problems, so display it is (k).
# actually we should rename this to show_post (k).
@atmos_student
def detail(request, pk_post):
    context_dict = {}
    context_dict['post'] = Post.objects.get(pk=pk_post)
    context_dict['comment_list'] = Comment.objects.filter(post=pk_post)
    return render(request, 'blog/detail.html', context_dict )


# show_category shows a single category and it's related posts.
@atmos_student
def show_category(request, category_name_slug):
    context_dict = {}
    try:
        category = Category.objects.get(slug=category_name_slug)
        post = Post.objects.filter(category=category)
        context_dict['post'] = post
        context_dict['category'] = category
    except Category.DoesNotExist:
        context_dict['category'] = None
        context_dict['post'] = None
    return render(request, 'blog/category.html', context_dict)


@atmos_student
def add_category(request):
    form = CategoryForm()

    if request.method == 'POST':
        form = CategoryForm(request.POST)
        if form.is_valid():
            cat = form.save(commit=True)
            print(cat, cat.slug)
            return index(request)
        else:
            print(form.errors)
    return render(request, 'blog/add_category.html', {'form':form})


@atmos_student
def add_post(request):
    return render(request, 'blog/add_post.html', {})

@atmos_student
def tasks(request):
    return render(request, 'blog/tasks.html', {})

@atmos_student
def the_study(request):

    return render(request, 'blog/the_study.html', {})


























