"""Shared by grant_staff and revoke_staff."""
import getpass
import logging

from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.db.models import Q

from staff import audit, roles


logger = logging.getLogger(__name__)


def server_actor():
    """Who ran the command, according to the server."""
    try:
        return 'server:{0}'.format(getpass.getuser())
    except Exception:
        return 'server'


def record(action, user, changes, reason=''):
    audit.record(None, action, user, reason=reason, changes=changes, actor_name=server_actor())


def notify(subject, body):
    """Tell all console members and developers about a role change."""
    members = User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(groups__name__in=roles.GROUP_NAMES)).distinct()
    to = sorted({u.email for u in members if u.email})
    if not to:
        return
    try:
        EmailMessage(subject=subject, body=body, from_email='atm-elsa@nmsu.edu', to=to).send()
    except Exception:
        logger.exception('role change notice failed')
