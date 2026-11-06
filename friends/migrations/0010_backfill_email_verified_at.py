from django.db import migrations
from django.db.models import Q


def forwards(apps, schema_editor):
    """Mark every account that has ever proved its address as verified.

    Before email_verified_at existed, finishing registration flipped is_active
    and the first sign-in stamped last_login. Either one means the code was
    entered. What is left, inactive and never signed in, is a sign-up that
    stopped at the code screen, and only those may resume verification.

    The stamp is only a marker, not a true verification time: the latest
    sign-in, or the join date for an account that is active but has never
    signed in (created in the admin, for example).
    """
    UserProfile = apps.get_model('friends', 'UserProfile')
    profiles = list(
        UserProfile.objects
        .select_related('user')
        .filter(email_verified_at__isnull=True)
        .filter(Q(user__is_active=True) | Q(user__last_login__isnull=False)))
    for profile in profiles:
        profile.email_verified_at = profile.user.last_login or profile.user.date_joined
    UserProfile.objects.bulk_update(profiles, ['email_verified_at'], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('friends', '0009_account_hardening'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
