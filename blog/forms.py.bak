# Stdlib imports

# Core Django imports
from django import forms

# Third-party app imports

# Imports from apps
from .models import Post, Comment, Category

# Blog forms

class PostForm(forms.ModelForm):
    # PostForm behaves like a normal blog post with more or less features
    # than you would expect from a normal blog.

    class Meta:
        model = Post
        fields = ('title', 'overview', 'content',)

class CategoryForm(forms.ModelForm):
    name = forms.CharField(help_text='Please enter the category name.')
    views = forms.IntegerField(widget=forms.HiddenInput(), initial=0)
    #likes = forms.IntegerField(widget=forms.HiddenInput(), initial=0)
    slug = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = Category
        fields = ('name',)

# This is forms.py where the Djnago forms live.  In models.py we saw a Task model object.  Here we are going to build an associated form.
# The basic structure is as follows...
#
# class TaskForm(forms.ModelForm)::
#     
#      # Attributes here 
#
#
#

