# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.conf import settings 
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import render, redirect
from django.views import generic
from .forms import UserForm, UserProfileForm
from .models import UserProfile
from build.models import Bundle
import os


#----------------------------------------------------------------------------------------


# Useful functions.
def makedirs(path):
    try:
        os.mkdir(path)
    except OSError as e:
        if e.errno ==17:
            # Dir already exists.  This shouldn't happen since pk is unique.
            print "Something is wrong.  Registration is trying to create two users with non-unique pk"
            pass

def check():
    print settings.MEDIA_DIR
    print os.path.join(settings.MEDIA_DIR, 'user')


#----------------------------------------------------------------------------------------










# Create your views here.




# Saving Team for when Teams are implemented
#@login_required
#class Team(generic.ListView):
#    model = User
#    context_object_name = 'friend_list'
    #queryset = User.objects.all()
#    template_name = 'friends/index.html'



# Normal profile page for users.  Displays the user, associated userprofile, and a list of related bundles.
@login_required
def profile(request, pk_user):
    context_dict = {}
    context_dict['userprofile'] = UserProfile.objects.get(pk=pk_user)
    context_dict['user'] = User.objects.get(userprofile=context_dict['userprofile'])
    context_dict['bundles'] = Bundle.objects.filter(user=context_dict['user'])
    context_dict['bundle_count'] = Bundle.objects.filter(user=context_dict['user']).count()

    if request.user == context_dict['user']:
        return render(request, 'friends/userprofile_detail.html', context_dict)

    else:
        return redirect('main:restricted_access')


    

# let's elsa's friends login
def friend_login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(username=username, password=password)

        if user:
            if user.is_active:
                # the User model is valid and active, so we can log the user in
                login(request, user)
                return HttpResponseRedirect(reverse('main:index'))
            else:
                # An inactive account was used - no logging in!
                return render(request, 'friends/inactive.html', {'user':user})
        else:
            # Bad login credentials
            return HttpResponse("Invalid login details supplied.")
    else:
        # Not a POST, so simply display the login form
        return render(request, 'friends/login.html', {})




# let's elsa's friends logout
@login_required
def friend_logout(request):
    logout(request)
    return HttpResponseRedirect(reverse('main:index'))



# let's people sign up to be one of elsa's friends
def register(request):
    # Boolean value.  Upon successful registration, registered will be changed to True
    registered = False

    user_form = UserForm(request.POST or None)
    profile_form = UserProfileForm(request.POST or None)

    if user_form.is_valid() and profile_form.is_valid():

        # Create User model object
        user = user_form.save()
        user.set_password(user.password)
        user.save()

        # Create User directory
        user_path = os.path.join(settings.ARCHIVE_DIR, user.username)
        makedirs(user_path)

        # Create UserProfile model object
        profile = profile_form.save(commit=False)
        profile.user = user
        profile.directory = user_path
        profile.save()
        registered = True

        #Login User
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return HttpResponseRedirect(reverse('main:index'))

        else:
            return HttpResponse("Error: Login after registration failed. Please contact <a href='{% url 'main:contact' %}'>Atmospheres Node</a> or try again.")        

    return render(request, 'friends/register.html', {'user_form':user_form, 'profile_form':profile_form, 'registered':registered})


# profile_settings NOTE:  It is important to NOT rename this as simply settings.  Since we import django.conf import settings, when our user goes to register, settings.ARCHIVE_DIR does not pull from our settings.py file.  Rather, Django comes to this function (if named settings) and notices there is no ARCHIVE_DIR declared here.  Big boo boo that cost me (k) a couple days to figure out.
@login_required
def friend_settings(request, pk_user):
    context_dict = {}
    context_dict['userprofile'] = UserProfile.objects.get(pk=pk_user)
    context_dict['user'] = User.objects.get(userprofile=context_dict['userprofile'])

    if request.user == context_dict['user']:
        return render(request, 'friends/settings.html', context_dict)

    else:
        return redirect('main:restricted_access')







