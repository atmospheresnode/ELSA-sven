import os
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.files.storage import default_storage
from .models import BundleSubmission
from django.core.mail import EmailMessage
from django.conf import settings
from django.utils import timezone

@login_required
def submit_main(request):
    history = BundleSubmission.objects.filter(user=request.user)
    return render(request, 'submit/submit_main.html', {'history': history})

@login_required
def upload_archive(request):
    if request.method == 'POST':
        file = request.FILES.get('archive_file')
        desc = request.POST.get('description')
        
        if file:
            # --- File validation step ---
            ext = os.path.splitext(file.name)[1].lower()
            valid_extensions = ['.zip', '.gz', '.tar']
            
            if ext not in valid_extensions:
                messages.error(request, f"Unsupported file type ({ext}). Please upload a .zip or .tar.gz file.")
                return redirect('submit:submit_main')
            # -----------------------
            
            # 1. Save the file to media/uploads/
            file_path = default_storage.save(f"uploads/{file.name}", file)
            
            # 2. Create the database record
            submission = BundleSubmission.objects.create(
                user=request.user,
                bundle_type=BundleSubmission.BundleType.ARCHIVE,
                filename=file.name,
                description=desc,
                status=BundleSubmission.StatusType.COMPLETED
            )
            
            # 3. Send Emails (User + Staff)
            send_upload_emails(request, submission)
            
            messages.success(request, "Archive bundle uploaded successfully!")
        else:
            messages.error(request, "No file selected.")
            
    return redirect('submit:submit_main')

@login_required
def upload_external(request):
    if request.method == 'POST':
        file = request.FILES.get('external_file')
        desc = request.POST.get('description')

        if file:
            ext = os.path.splitext(file.name)[1].lower()
            valid_extensions = ['.zip', '.gz', '.tar']
            
            if ext not in valid_extensions:
                messages.error(request, f"Unsupported file type ({ext}). Please upload a .zip or .tar.gz file.")
                return redirect('submit:submit_main')
            
            # 1. Save the file
            file_path = default_storage.save(f"uploads/{file.name}", file)

            # 2. Create the database record
            submission = BundleSubmission.objects.create(
                user=request.user,
                bundle_type=BundleSubmission.BundleType.EXTERNAL,
                filename=file.name,
                description=desc,
                status=BundleSubmission.StatusType.COMPLETED
            )

            # 3. Send Emails (User + Staff)
            send_upload_emails(request, submission)

            messages.success(request, "External bundle uploaded successfully!")
        else:
             messages.error(request, "No file selected.")

    return redirect('submit:submit_main')


# --- HELPER FUNCTION TO SEND EMAILS ---
def send_upload_emails(request, submission):
    
    uploaded_filename = submission.filename
    user_email = request.user.email
    bundle_type_label = submission.get_bundle_type_display() # Gets "Archive" or "External"

    # --- Email 1: To the User (Confirmation) ---
    user_subject = f"ELSA Submission Received: {uploaded_filename}"
    user_body = f"""
    Hello {request.user.first_name},

    This is a confirmation that your {bundle_type_label} file '{uploaded_filename}' has been successfully uploaded to the ELSA system.
    
    Our team has been notified and will review the submission shortly.
    
    Thank you,
    The ELSA Team
    """
    
    email_to_user = EmailMessage(
        subject=user_subject,
        body=user_body,
        from_email='atm-elsa@nmsu.edu',
        to=[user_email]
    )
    email_to_user.send(fail_silently=True)

    # --- Email 2: To ELSA Staff (Notification) ---
    staff_subject = f"New ELSA Upload ({bundle_type_label}): {request.user.username}"
    
    # FIX: Use timezone.now() instead of submission.created_at
    current_time = timezone.now().strftime("%Y-%m-%d %H:%M:%S") 

    staff_body = f"""
    A new file has been uploaded to ELSA.

    User: {request.user.username} ({request.user.email})
    Type: {bundle_type_label}
    File Name: {uploaded_filename}
    Description: {submission.description}
    Upload Time: {current_time}
    
    Please check the server/queue for processing.
    """
    
    # Staff email list
    #staff_emails = ['atm-elsa@nmsu.edu', 'sajomont@nmsu.edu', 'rupakdey@nmsu.edu']
    staff_emails = ['rupakdey@nmsu.edu'] # Temporary: Send only to Rupak for testing
    
    email_to_staff = EmailMessage(
        subject=staff_subject,
        body=staff_body,
        from_email='atm-elsa@nmsu.edu',
        to=staff_emails,
        headers={'Reply-To': user_email}
    )
    email_to_staff.send(fail_silently=True)