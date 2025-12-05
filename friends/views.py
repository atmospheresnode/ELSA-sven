# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from email.message import EmailMessage

from django.core.mail import EmailMessage
from django.conf import settings 
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import render, redirect
from django.views import generic
from .forms import *
from .models import *
from build.models import Bundle
import os

from django.utils import timezone
from datetime import timedelta

#----------------------------------------------------------------------------------------


# Useful functions.
def makedirs(path):
    try:
        os.mkdir(path)
    except OSError as e:
        if e.errno ==17:
            # Dir already exists.  This shouldn't happen since pk is unique.
            print("Something is wrong.  Registration is trying to create two users with non-unique pk")
            pass

def check():
    print(settings.MEDIA_DIR)
    print(os.path.join(settings.MEDIA_DIR, 'user'))


#----------------------------------------------------------------------------------------










# Create your views here.




# Saving Team for when Teams are implemented
#@login_required
#class Team(generic.ListView):
#    model = User
#    context_object_name = 'friend_list'
    #queryset = User.objects.all()
#    template_name = 'friends/index.html'



# Redirecting accounts/login to elsa/ (taking the user to the new UI)
def redirect_to_elsa_home(request): 
    return HttpResponseRedirect(reverse('main:index'))



# Normal profile page for users.  Displays the user, associated userprofile, and a list of related bundles.
@login_required
def profile(request, pk_user):
    
    context_dict = {}
    context_dict['userprofile'] = UserProfile.objects.get(pk=pk_user)
    context_dict['user'] = User.objects.get(userprofile=context_dict['userprofile'])
    context_dict['bundles'] = Bundle.objects.filter(user=context_dict['user'])
    context_dict['bundle_count'] = Bundle.objects.filter(user=context_dict['user']).count()
    context_dict['archive_bundles'] = context_dict['bundles'].filter(bundle_type='Archive')
    context_dict['external_bundles'] = context_dict['bundles'].filter(bundle_type='External')

    #This block checks that all bundles actually exist in the archive-
    #if not, it deletes that bundle from the database.
    for b in context_dict['bundles']:
        if os.path.isdir(b.directory()):
            pass
        else:
            b.remove_bundle()
            context_dict['bundles'] = Bundle.objects.filter(user=context_dict['user'])
            context_dict['bundle_count'] = Bundle.objects.filter(user=context_dict['user']).count()


    if request.user == context_dict['user']:
        return render(request, 'friends/bundle_hub.html', context_dict)

    else:
        return redirect('main:restricted_access')



# let's elsa's friends login
def friend_login(request):
    """
    Validates credentials -> Generates OTP -> Redirects to Verify Page.
    Does NOT log the user in immediately.
    """
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(username=username, password=password)

        if user:
            if user.is_active:
                try:
                    profile = user.userprofile
                except UserProfile.DoesNotExist:
                    user_path = os.path.join(settings.ARCHIVE_DIR, user.username)
                    makedirs(user_path)
                    profile = UserProfile.objects.create(
                        user=user, 
                        directory=user_path
                    )

                otp = profile.generate_otp()

                print(f"DEBUG: OTP for {user.username} is: {otp}")
                
                email = EmailMessage(
                    subject="ELSA Login Verification",
                    body=f"Your one-time login code is: {otp}",
                    from_email='atm-elsa@nmsu.edu',
                    to=[user.email]
                )
                email.send(fail_silently=True)

                request.session['pre_otp_user_id'] = user.id

                return redirect('friends:otp_verify')

            else:
                return render(request, 'friends/inactive.html', {'user':user})
        else:
            messages.error(request, "Invalid username or password.")
            return render(request, 'main:index')
    else:
        # On GET, show the login form
        return render(request, 'main:index')


# friends/views.py

def otp_verify(request):
    """
    Verifies OTP, Activates User (if new), and Logs them in.
    """
    user_id = request.session.get('pre_otp_user_id')
    
    if not user_id:
        messages.error(request, "Session expired. Please login or register again.")
        return redirect('friends:login')
    
    if request.method == 'POST':
        otp_input = request.POST.get('otp')
        
        try:
            user = User.objects.get(id=user_id)
            profile = user.userprofile
            
            if profile.otp_code == otp_input:
                if profile.otp_created_at > timezone.now() - timedelta(minutes=5):
                    
                    user.is_active = True 
                    user.save()

                    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                    
                    del request.session['pre_otp_user_id']
                    profile.otp_code = None
                    profile.save()
                    
                    messages.success(request, f"Welcome to ELSA, {user.username}!")
                    return HttpResponseRedirect(reverse('main:index'))
                else:
                    messages.error(request, "This code has expired.")
            else:
                messages.error(request, "Invalid code. Please try again.")
                
        except User.DoesNotExist:
            messages.error(request, "User error.")
            return redirect('friends:login')

    return render(request, 'friends/otp_verify.html')




# let's elsa's friends logout
@login_required
def friend_logout(request):
    logout(request)
    return HttpResponseRedirect(reverse('main:index'))



# let's people sign up to be one of elsa's friends
# friends/views.py

# friends/views.py

def register(request):
    registered = False

    user_form = UserForm(request.POST or None)
    profile_form = UserProfileForm(request.POST or None)

    if user_form.is_valid() and profile_form.is_valid():

   
        user = user_form.save(commit=False)
        user.set_password(user.password)
        user.is_active = False 
        user.save() 

  
        user_path = os.path.join(settings.ARCHIVE_DIR, user.username)
        makedirs(user_path)

    
        profile = user.userprofile 
        
    
        profile.agency = profile_form.cleaned_data.get('agency')
        profile.directory = user_path
        profile.save()
        
        registered = True


        otp = profile.generate_otp()
        
  
        print(f"DEBUG: Registration OTP for {user.username} is: {otp}")
        email = EmailMessage(
            subject="ELSA Email Verification",
            body=f"Welcome to ELSA! Please verify your email with this code: {otp}",
            from_email='atm-elsa@nmsu.edu',
            to=[user.email]
        )
        email.send(fail_silently=True)


        request.session['pre_otp_user_id'] = user.id

    
        return redirect('friends:otp_verify')       

    return render(request, 'friends/register.html', {'user_form':user_form, 'profile_form':profile_form, 'registered':registered})


# user account page
@login_required
def friend_useraccount(request):
    context_dict = {}
    context_dict['bundle_count'] = Bundle.objects.filter(user=request.user).count()
    return render(request, 'friends/useraccount.html', context_dict)


# profile_settings NOTE:  It is important to NOT rename this as simply settings.  Since we import django.conf import settings, when our user goes to register, settings.ARCHIVE_DIR does not pull from our settings.py file.  Rather, Django comes to this function (if named settings) and notices there is no ARCHIVE_DIR declared here.  Big boo boo that cost me (k) a couple days to figure out.
@login_required
def friend_settings(request, pk_user):

    updated = False # This is a flag to determine if the user has updated their profile.
    context_dict = {}
    context_dict['userprofile'] = UserProfile.objects.get(pk=pk_user)
    context_dict['user'] = User.objects.get(userprofile=context_dict['userprofile'])
    
    user = context_dict['user']
    userProfile = context_dict['userprofile']

    first_form = UpdateNameFirstForm(request.POST or None)
    last_form = UpdateNameLastForm(request.POST or None)
    agency_form = UpdateAgencyForm(request.POST or None)
    email_form = UpdateEmailForm(request.POST or None)
    password_form = UpdatePasswordForm(request.POST or None)
    
    if first_form.is_valid():
        nameF = first_form.save()
        user.first_name = nameF.first_name
        user.save()
        updated = True

    if last_form.is_valid():
        nameL = last_form.save()
        user.last_name = nameL.last_name
        user.save()
        updated = True

    if email_form.is_valid():
        email = email_form.save()
        user.email = email.email
        user.save()
        updated = True

    if password_form.is_valid():
        pwdForm = password_form.save()
        if user.check_password(pwdForm.current_password):
            print("Valid")
            if pwdForm.new_password == pwdForm.confirm_password:
                print("Valid")
                user.set_password(pwdForm.new_password)
                user.save()
                updated = True

            else:
                    return render(request, 'friends/settings/mismatched_password.html', context_dict)
        else:
            return render(request, 'friends/settings/wrong_password.html', context_dict)

    if updated == True:
        email_user = EmailMessage(
            subject = "ELSA User Profile Updated",
            body = 'Your ELSA user profile has been updated. If you did not make this change, please visit https://atmos.nmsu.edu/elsa/contact/ to report this incident. Thank you for using ELSA! \n\nRegards,\nTeam ELSA',
            from_email = 'atm-elsa@nmsu.edu',
            to=[user.email]
        )
        email_user.send()

    if request.user == context_dict['user']:
        return render(request, 'friends/settings.html', context_dict)

    else:
        return redirect('main:restricted_access')
    

@login_required
def bundle_hub(request):
    bundles = Bundle.objects.filter(user=request.user)
    return render(request, 'friends/bundle_hub.html', {'bundles': bundles})

@login_required
def delete_bundles(request):
    if request.method == "POST":
        bundle_ids = request.POST.getlist('bundle_ids')
        if not bundle_ids:
            messages.warning(request, "No bundles were selected.")
            return redirect('friends:bundle_hub')  # redirect back to hub

        bundles = Bundle.objects.filter(id__in=bundle_ids, user=request.user)
        if not bundles.exists():
            messages.error(request, "You cannot delete bundles that do not belong to you.")
            return redirect('friends:bundle_hub')

        # call remove_bundle() for each before deleting
        for bundle in bundles:
            bundle.remove_bundle()
        count = bundles.count()
        bundles.delete()

        messages.success(request, f"{count} bundle(s) deleted successfully.")
        return redirect('friends:bundle_hub')

    return redirect('friends:bundle_hub')