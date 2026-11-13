# -*- coding: utf-8 -*-
"""Admin Console models.

A Case is something staff have to act on: an agency request, a submitted bundle, a peer
review. An AuditEntry is something staff did. Audit entries can't be updated or deleted.
"""
from __future__ import unicode_literals

import secrets
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


def new_reply_token():
    return secrets.token_hex(10)


class Case(models.Model):
    AGENCY = 'agency'
    SUBMISSION = 'submission'
    UPLOAD = 'upload'
    PEER_REVIEW = 'peer_review'
    CONTACT = 'contact'
    STORAGE = 'storage'
    CLOSURE = 'closure'
    FEEDBACK = 'feedback'
    MESSAGE = 'message'
    UPDATE = 'update'
    KIND_CHOICES = (
        (AGENCY, 'Agency change'),
        (SUBMISSION, 'Bundle submission'),
        (UPLOAD, 'Bundle upload'),
        (PEER_REVIEW, 'Peer review'),
        (CONTACT, 'Contact message'),
        (STORAGE, 'Storage limit'),
        (CLOSURE, 'Account closed'),
        (FEEDBACK, 'Feedback'),
        (MESSAGE, 'Conversation'),
        (UPDATE, 'Outside update'),
    )

    OPEN = 'open'
    IN_PROGRESS = 'in_progress'
    WAITING = 'waiting'
    RESOLVED = 'resolved'
    DECLINED = 'declined'
    STATUS_CHOICES = (
        (OPEN, 'Open'),
        (IN_PROGRESS, 'In progress'),
        (WAITING, 'Waiting on user'),
        (RESOLVED, 'Resolved'),
        (DECLINED, 'Declined'),
    )
    ACTIVE = (OPEN, IN_PROGRESS, WAITING)
    CLOSED = (RESOLVED, DECLINED)

    # Where a submitted bundle is in review.  Only submission cases use it.
    STAGE_SUBMITTED = 'submitted'
    STAGE_UNDER_REVIEW = 'under_review'
    STAGE_CHANGES = 'changes_requested'
    STAGE_ACCEPTED = 'accepted'
    STAGE_RELEASED = 'released'
    STAGE_NOT_ACCEPTED = 'not_accepted'
    STAGE_CHOICES = (
        (STAGE_SUBMITTED, 'Submitted'),
        (STAGE_UNDER_REVIEW, 'Under review'),
        (STAGE_CHANGES, 'Changes requested'),
        (STAGE_ACCEPTED, 'Accepted'),
        (STAGE_RELEASED, 'Released'),
        (STAGE_NOT_ACCEPTED, 'Not accepted'),
    )

    kind = models.CharField(max_length=20, choices=KIND_CHOICES, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=OPEN, db_index=True)
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, blank=True, default='')
    title = models.CharField(max_length=255)
    # What the requester wrote, verbatim.  Shown as text, never as HTML.
    body = models.TextField(blank=True, default='')

    # Copied so the case still says who asked after the account is closed.
    # Anonymous contact messages have no user.
    requester = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='staff_cases')
    requester_username = models.CharField(max_length=150, blank=True, default='')
    requester_name = models.CharField(max_length=255, blank=True, default='')
    requester_email = models.EmailField(blank=True, default='')

    bundle = models.ForeignKey('build.Bundle', null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='staff_cases')
    # Type-specific facts written by ELSA, e.g. the agency asked for.
    data = models.JSONField(default=dict, blank=True)

    assignee = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='assigned_cases')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='closed_cases')

    # Routes an emailed answer back to this case. Not a login, the sender's address still has to match.
    reply_token = models.CharField(max_length=32, unique=True, null=True, blank=True)
    # First staff answer ("needs first response", reports) and last requester message ("needs reply").
    first_response_at = models.DateTimeField(null=True, blank=True)
    last_user_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_spam = models.BooleanField(default=False, db_index=True)
    # Answers to the review checklist, {item_key: true/false}.
    checklist = models.JSONField(default=dict, blank=True)
    # When the stage last changed, for "days in this stage".
    stage_changed_at = models.DateTimeField(null=True, blank=True)

    class Meta(object):
        ordering = ('-updated_at', '-pk')
        # Only these.  The console never uses Django's add/change/delete/view.
        default_permissions = ()
        permissions = (
            ('view_console', 'Can open the Admin Console and read everything in it'),
            ('work_cases', 'Can claim, answer and close cases'),
            ('decide_agency', 'Can change a user\'s agency'),
            ('review_bundles', 'Can move submitted bundles through review'),
            ('manage_accounts', 'Can disable accounts and sign users out'),
            ('view_audit', 'Can read the audit log'),
        )

    def __str__(self):
        return '#{0} {1}'.format(self.pk, self.title)

    def save(self, *args, **kwargs):
        if not self.reply_token:
            self.reply_token = new_reply_token()
        super(Case, self).save(*args, **kwargs)

    @property
    def is_active(self):
        return self.status in self.ACTIVE

    def requester_label(self):
        if self.kind == self.UPDATE and not (self.requester_name or self.requester_username):
            return "ELSA's daily update check"
        return self.requester_name or self.requester_username or self.requester_email or 'Anonymous'

    def user_status(self):
        """(label, badge classes) as the requester sees it."""
        return {
            self.OPEN: ('With the team', 'bg-primary'),
            self.IN_PROGRESS: ('With the team', 'bg-primary'),
            self.WAITING: ('Waiting on you', 'bg-warning text-dark'),
            self.RESOLVED: ('Resolved', 'bg-success'),
            self.DECLINED: ('Declined', 'bg-secondary'),
        }.get(self.status, (self.get_status_display(), 'bg-secondary'))

    def user_status_label(self):
        return self.user_status()[0]

    def user_status_badge(self):
        return self.user_status()[1]

    def status_badge(self):
        """Bootstrap badge classes, matching the hub's colours."""
        # Red is kept for overdue age, so it still means something.
        return {
            self.OPEN: 'bg-light text-dark border',
            self.IN_PROGRESS: 'bg-primary',
            self.WAITING: 'bg-warning text-dark',
            self.RESOLVED: 'bg-success',
            self.DECLINED: 'bg-secondary',
        }.get(self.status, 'bg-secondary')


class CaseNote(models.Model):
    NOTE = 'note'
    REPLY = 'reply'
    EVENT = 'event'
    MESSAGE = 'message'
    KIND_CHOICES = (
        (NOTE, 'Internal note'),
        (REPLY, 'Reply to user'),
        (EVENT, 'Event'),
        (MESSAGE, 'From the user'),
    )

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name='notes')
    author = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='+')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=NOTE)
    body = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta(object):
        ordering = ('created_at', 'pk')
        default_permissions = ()

    def __str__(self):
        return '{0} on case {1}'.format(self.get_kind_display(), self.case_id)


class Lien(models.Model):
    """A problem from a peer or bundle review that has to be fixed before the data is accepted."""
    OPEN = 'open'
    RESOLVED = 'resolved'
    WAIVED = 'waived'
    STATUS_CHOICES = (
        (OPEN, 'Open'),
        (RESOLVED, 'Resolved'),
        (WAIVED, 'Waived'),
    )

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name='liens')
    description = models.TextField()
    # Optional file (relative to the bundle folder) and line this is about.
    file_path = models.CharField(max_length=500, blank=True, default='')
    line = models.PositiveIntegerField(null=True, blank=True)
    # The owner says it is fixed; staff still confirm it before it resolves.
    owner_fixed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OPEN)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    closed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='+')
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta(object):
        ordering = ('created_at', 'pk')
        default_permissions = ()

    def __str__(self):
        return 'Lien {0} on case {1}'.format(self.pk, self.case_id)


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError('Audit entries cannot be changed.')

    def delete(self):
        raise PermissionError('Audit entries cannot be deleted.')


class AuditEntry(models.Model):
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name='+')
    # Kept as text so the entry still has a name if the account is deleted.
    actor_username = models.CharField(max_length=150)
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=32, blank=True, default='', db_index=True)
    target_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    target_label = models.CharField(max_length=255, blank=True, default='')
    reason = models.TextField(blank=True, default='')
    # {"field": [before, after]} for changes, or any facts worth keeping.
    changes = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta(object):
        ordering = ('-created_at', '-pk')
        default_permissions = ()
        verbose_name_plural = 'audit entries'

    def __str__(self):
        return '{0} {1} {2}'.format(self.actor_username, self.action, self.target_label)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError('Audit entries cannot be changed.')
        super(AuditEntry, self).save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError('Audit entries cannot be deleted.')


class AuditSeal(models.Model):
    """Tamper check for the audit log. Each seal hashes its entry plus the previous seal.

    `manage.py verify_audit` runs nightly and reports any break. See staff.audit.
    """
    entry = models.OneToOneField(AuditEntry, on_delete=models.PROTECT, related_name='seal')
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta(object):
        ordering = ('pk',)
        default_permissions = ()

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError('Audit seals cannot be changed.')
        super(AuditSeal, self).save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError('Audit seals cannot be deleted.')


class AuditChain(models.Model):
    """Row every audit write locks. Also keeps the newest seal, to catch entries removed from the end."""
    head_hash = models.CharField(max_length=64, blank=True, default='')
    count = models.PositiveIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_ok = models.BooleanField(default=True)
    problem = models.CharField(max_length=500, blank=True, default='')

    class Meta(object):
        default_permissions = ()


class ConsoleSnapshot(models.Model):
    """Slow lists computed by `manage.py staff_precompute`, so pages don't walk every bundle folder."""
    key = models.CharField(max_length=40, unique=True)
    data = models.JSONField(default=dict, blank=True)
    computed_at = models.DateTimeField(default=timezone.now)

    class Meta(object):
        default_permissions = ()



class CaseRead(models.Model):
    """When someone last looked at a case, for unread markers. Kept per side since staff can also be requesters."""
    STAFF = 'staff'
    USER = 'user'
    ROLE_CHOICES = ((STAFF, 'Staff'), (USER, 'User'))

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name='reads')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+')
    role = models.CharField(max_length=8, choices=ROLE_CHOICES, default=STAFF)
    seen_at = models.DateTimeField(default=timezone.now)

    class Meta(object):
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=['case', 'user', 'role'], name='staff_caseread_once_per_side')]


class SavedReply(models.Model):
    """A reply staff send often.  Placeholders are filled in on the case page."""
    title = models.CharField(max_length=120)
    body = models.TextField()
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta(object):
        ordering = ('title',)
        default_permissions = ()

    def __str__(self):
        return self.title


class StaffPreference(models.Model):
    """Which emails a staff member wants. All on by default."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='staff_preference')
    daily_digest = models.BooleanField(default=True)
    when_assigned = models.BooleanField(default=True)
    when_user_replies = models.BooleanField(default=True)
    # Messages on unassigned requests. Off by default since they're under the bell and on Today.
    when_unassigned = models.BooleanField(default=False)
    when_mentioned = models.BooleanField(default=True)
    # Email for a new version of something ELSA depends on (staff.watchers).
    when_update = models.BooleanField(default=True)
    # Out of office: shown wherever someone might hand them work.
    away_until = models.DateField(null=True, blank=True)

    class Meta(object):
        default_permissions = ()

    @classmethod
    def for_user(cls, user):
        return cls.objects.get_or_create(user=user)[0]


class BlockedSender(models.Model):
    """An address whose contact-form messages go straight to spam."""
    email = models.EmailField(unique=True)
    reason = models.CharField(max_length=255, blank=True, default='')
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta(object):
        ordering = ('email',)
        default_permissions = ()

    def save(self, *args, **kwargs):
        self.email = (self.email or '').strip().lower()
        super(BlockedSender, self).save(*args, **kwargs)


class SubmissionSnapshot(models.Model):
    """A bundle's files when it was submitted, for "changed since"."""
    bundle = models.ForeignKey('build.Bundle', on_delete=models.CASCADE, related_name='snapshots')
    taken_at = models.DateTimeField(default=timezone.now)
    # {relative path: [size, sha256]}
    manifest = models.JSONField(default=dict)

    class Meta(object):
        ordering = ('-taken_at', '-pk')
        default_permissions = ()


class ReviewCampaign(models.Model):
    """Peer review of one data set by invited reviewers. Liens are on a Case."""
    OPEN = 'open'
    CLOSED = 'closed'
    STATUS_CHOICES = ((OPEN, 'Open'), (CLOSED, 'Closed'))

    title = models.CharField(max_length=200)
    data_set = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    deadline = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OPEN)
    case = models.OneToOneField(Case, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='campaign')
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta(object):
        ordering = ('-created_at', '-pk')
        default_permissions = ()

    def __str__(self):
        return self.title


class ReviewerInvite(models.Model):
    """Invited reviewer. No account needed, the emailed link has a token and we only store its SHA-256."""
    INVITED = 'invited'
    OPENED = 'opened'
    SUBMITTED = 'submitted'
    REVOKED = 'revoked'
    STATUS_CHOICES = ((INVITED, 'Invited'), (OPENED, 'Opened'), (SUBMITTED, 'Submitted'),
                      (REVOKED, 'Revoked'))

    campaign = models.ForeignKey(ReviewCampaign, on_delete=models.CASCADE, related_name='invites')
    name = models.CharField(max_length=150)
    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=INVITED)
    answers = models.JSONField(default=dict, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta(object):
        ordering = ('name', 'pk')
        default_permissions = ()

    def __str__(self):
        return '{0} for {1}'.format(self.email, self.campaign_id)


class Announcement(models.Model):
    """A banner shown across ELSA between two times."""
    INFO = 'info'
    WARNING = 'warning'
    LEVEL_CHOICES = ((INFO, 'Information'), (WARNING, 'Warning'))

    title = models.CharField(max_length=150)
    body = models.TextField(blank=True, default='')
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default=INFO)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta(object):
        ordering = ('-starts_at', '-pk')
        default_permissions = ()

    def __str__(self):
        return self.title


class AnnouncementDismissal(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name='dismissals')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+')

    class Meta(object):
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=['announcement', 'user'],
                                               name='staff_dismissal_once')]


class Broadcast(models.Model):
    """One email sent to a group of users."""
    SEGMENT_CHOICES = (
        ('all', 'Everyone with an active account'),
        ('external', 'Owners of External bundles'),
        ('archive', 'Owners of Archive bundles'),
        ('submitted', 'Owners of submitted bundles'),
        ('staff', 'Admin Console members (for testing)'),
    )
    DRAFT = 'draft'
    SENDING = 'sending'
    SENT = 'sent'
    STATUS_CHOICES = ((DRAFT, 'Draft'), (SENDING, 'Sending'), (SENT, 'Sent'))

    subject = models.CharField(max_length=150)
    body = models.TextField()
    segment = models.CharField(max_length=20, choices=SEGMENT_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    recipient_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    # Sent by its own process (manage.py send_broadcast), not the web request.
    # These track the group size, the last account handled, and the sender's lease.
    # If a sender dies the cursor stays put, so a resume skips people who already got it.
    total = models.PositiveIntegerField(default=0)
    cursor = models.PositiveIntegerField(default=0)
    worker = models.CharField(max_length=32, blank=True, default='')
    heartbeat_at = models.DateTimeField(null=True, blank=True)

    # Senders write after every message. No write for this long means it's gone.
    STALL_AFTER = timedelta(minutes=5)

    class Meta(object):
        ordering = ('-created_at', '-pk')
        default_permissions = ()

    def stalled(self):
        """Still sending but nothing sent for a while, so it can be resumed."""
        return (self.status == self.SENDING
                and (self.heartbeat_at is None or self.heartbeat_at < timezone.now() - self.STALL_AFTER))

    def handled(self):
        return self.recipient_count + self.failed_count

    def remaining(self):
        return max(0, self.total - self.handled())


class MailLog(models.Model):
    """Every email handed to the mail server, when the logging backend is on."""
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    subject = models.CharField(max_length=255)
    to = models.TextField()
    from_email = models.CharField(max_length=255, blank=True, default='')
    sent = models.BooleanField(default=True)
    error = models.TextField(blank=True, default='')

    class Meta(object):
        ordering = ('-created_at', '-pk')
        default_permissions = ()


class StaffSession(models.Model):
    """A browser in admin mode, for "who is in admin now" and new device alerts."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+')
    session_key = models.CharField(max_length=40, unique=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True, default='')
    elevated_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta(object):
        ordering = ('-last_seen_at',)
        default_permissions = ()



class Notification(models.Model):
    """Something a user should know about. Shows under the bell and is usually emailed."""
    REPLY = 'reply'
    OUTCOME = 'outcome'
    REVIEW = 'review'
    LIEN = 'lien'
    MESSAGE = 'message'
    ACCOUNT = 'account'
    UPDATE = 'update'
    KIND_CHOICES = ((REPLY, 'Reply'), (OUTCOME, 'Outcome'), (REVIEW, 'Review'), (LIEN, 'Item to fix'),
                    (MESSAGE, 'Message'), (ACCOUNT, 'Account'), (UPDATE, 'Label update'))

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='elsa_notifications')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=300, blank=True, default='')
    # Always a path inside ELSA; never an outside address.
    url = models.CharField(max_length=300, blank=True, default='')
    case = models.ForeignKey(Case, null=True, blank=True, on_delete=models.CASCADE, related_name='notifications')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta(object):
        ordering = ('-created_at', '-pk')
        default_permissions = ()

    def __str__(self):
        return '{0}: {1}'.format(self.user_id, self.title)


class UserNotificationPreference(models.Model):
    """Which optional emails a user gets. Account and security notices always go.

    ``email``: replies and news about their own requests and reviews.
    ``news``: mass emails. Can also be turned off from the unsubscribe link.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='elsa_notification_preference')
    email = models.BooleanField(default=True)
    news = models.BooleanField(default=True)
    news_changed_at = models.DateTimeField(null=True, blank=True)

    class Meta(object):
        default_permissions = ()

    @classmethod
    def wants_email(cls, user):
        value = cls.objects.filter(user=user).values_list('email', flat=True).first()
        return True if value is None else value

    @classmethod
    def wants_news(cls, user):
        value = cls.objects.filter(user=user).values_list('news', flat=True).first()
        return True if value is None else value

    @classmethod
    def set_news(cls, user, on):
        cls.objects.update_or_create(user=user, defaults={'news': bool(on), 'news_changed_at': timezone.now()})


class WatchedSource(models.Model):
    """Something ELSA depends on (staff.watchers). Written by `manage.py staff_watch`."""
    key = models.CharField(max_length=40, unique=True)
    current = models.CharField(max_length=100, blank=True, default='')
    latest = models.CharField(max_length=100, blank=True, default='')
    latest_url = models.CharField(max_length=500, blank=True, default='')
    note = models.CharField(max_length=500, blank=True, default='')
    # Extra facts from the source, e.g. {'eol': '2026-04-07'} for Django.
    details = models.JSONField(default=dict, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    ok_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=500, blank=True, default='')
    # Days in a row the source couldn't be read, and whether the team was told.
    failures = models.PositiveIntegerField(default=0)
    failure_case = models.ForeignKey(Case, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta(object):
        ordering = ('key',)
        default_permissions = ()

    def __str__(self):
        return self.key



class SnapshotFile(models.Model):
    """zlib-compressed text of a small label or text file at submission, to compare on re-review."""
    snapshot = models.ForeignKey(SubmissionSnapshot, on_delete=models.CASCADE, related_name='files')
    path = models.CharField(max_length=500)
    data = models.BinaryField()

    class Meta(object):
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=['snapshot', 'path'], name='staff_snapshotfile_once')]

    def text(self):
        import zlib
        return zlib.decompress(bytes(self.data)).decode('utf-8', errors='replace')


class Project(models.Model):
    """Archiving project with a provider: its bundles, the people involved and due dates."""
    PLANNING = 'planning'
    ACTIVE = 'active'
    DONE = 'done'
    STATUS_CHOICES = ((PLANNING, 'Planning'), (ACTIVE, 'Active'), (DONE, 'Done'))

    name = models.CharField(max_length=150)
    mission = models.CharField(max_length=150, blank=True, default='')
    description = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=ACTIVE)
    staff = models.ManyToManyField(User, related_name='staff_projects', blank=True)
    providers = models.ManyToManyField(User, related_name='provider_projects', blank=True)
    external_contacts = models.TextField(blank=True, default='')
    bundles = models.ManyToManyField('build.Bundle', related_name='projects', blank=True)
    # Reminders always go to staff. Providers only get them when this is on.
    remind_providers = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta(object):
        ordering = ('name',)
        default_permissions = ()

    def __str__(self):
        return self.name


class ProjectMilestone(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='milestones')
    title = models.CharField(max_length=200)
    due_date = models.DateField()
    done_at = models.DateTimeField(null=True, blank=True)
    done_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    # Last reminder sent, so the daily job doesn't send twice in a day.
    reminded_on = models.DateField(null=True, blank=True)

    class Meta(object):
        ordering = ('due_date', 'pk')
        default_permissions = ()

    def __str__(self):
        return self.title


class Release(models.Model):
    """One accepted bundle on its way into the PDS archive, step by step."""
    DOI_NONE = ''
    DOI_RESERVED = 'reserved'
    DOI_REGISTERED = 'registered'
    DOI_CHOICES = ((DOI_NONE, 'None yet'), (DOI_RESERVED, 'Reserved'), (DOI_REGISTERED, 'Registered'))
    REGISTRY_CHOICES = (('', 'Not yet'), ('submitted', 'Submitted to the registry'), ('registered', 'In the registry'))

    bundle = models.ForeignKey('build.Bundle', on_delete=models.CASCADE, related_name='releases')
    case = models.ForeignKey(Case, null=True, blank=True, on_delete=models.SET_NULL, related_name='releases')
    label = models.CharField(max_length=60, blank=True, default='')
    final_run = models.ForeignKey('build.ValidationRun', null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='+')
    package_path = models.CharField(max_length=500, blank=True, default='')
    package_size = models.BigIntegerField(default=0)
    package_sha256 = models.CharField(max_length=64, blank=True, default='')
    package_files = models.PositiveIntegerField(default=0)
    package_built_at = models.DateTimeField(null=True, blank=True)
    doi = models.CharField(max_length=200, blank=True, default='')
    doi_status = models.CharField(max_length=12, choices=DOI_CHOICES, blank=True, default='')
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_note = models.TextField(blank=True, default='')
    registry_status = models.CharField(max_length=12, choices=REGISTRY_CHOICES, blank=True, default='')
    released_at = models.DateTimeField(null=True, blank=True)
    release_note = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta(object):
        ordering = ('-created_at', '-pk')
        default_permissions = ()

    def __str__(self):
        return 'Release {0} of {1}'.format(self.label or self.pk, self.bundle_id)

    def steps(self):
        """(key, label, done) in order, for the checklist on the release page."""
        return [
            ('validate', 'Final full validate with no errors',
             bool(self.final_run_id and self.final_run.status == 'd' and not self.final_run.error_count)),
            ('package', 'Delivery package built', bool(self.package_built_at)),
            ('doi', 'DOI registered', self.doi_status == self.DOI_REGISTERED),
            ('delivered', 'Delivered to the Engineering Node', bool(self.delivered_at)),
            ('registry', 'In the PDS registry', self.registry_status == 'registered'),
            ('released', 'Released', bool(self.released_at)),
        ]


class KnowledgeNote(models.Model):
    """Team notes on how we handle a mission, a format or a common question."""
    title = models.CharField(max_length=200)
    topic = models.CharField(max_length=100, blank=True, default='')
    body = models.TextField()
    pinned = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta(object):
        ordering = ('-pinned', 'topic', 'title')
        default_permissions = ()

    def __str__(self):
        return self.title


class SavedView(models.Model):
    """Named set of filters on a console list, private or shared."""
    PAGES = (('staff:home', 'Queue'), ('staff:users', 'Users'), ('staff:bundles', 'Bundles'),
             ('staff:pipeline', 'Pipeline'))
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='saved_views')
    name = models.CharField(max_length=80)
    page = models.CharField(max_length=30, choices=PAGES)
    query = models.CharField(max_length=500, blank=True, default='')
    shared = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta(object):
        ordering = ('name',)
        default_permissions = ()


class CasePresence(models.Model):
    """Who has a case open right now, from the page's heartbeat."""
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name='presence')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+')
    seen_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta(object):
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=['case', 'user'], name='staff_presence_once')]


class ConsoleSetting(models.Model):
    """Team-wide console settings, like two-person sign-off."""
    key = models.CharField(max_length=60, unique=True)
    value = models.JSONField(default=dict)
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta(object):
        default_permissions = ()

    DEFAULTS = {
        'two_person_acceptance': False,
        'first_reply_days': 2,
        'stalled_days': 30,
        'pipeline_stuck_days': 14,
    }

    @classmethod
    def get(cls, key):
        row = cls.objects.filter(key=key).values_list('value', flat=True).first()
        if isinstance(row, dict) and 'v' in row:
            return row['v']
        return cls.DEFAULTS.get(key)
