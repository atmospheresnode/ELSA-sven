from django.db import migrations


# What UserProfile.agency is allowed to hold: the VALUES from AGENCY_CHOICES.
VALID = ('nasa:pds', 'esa:psa', 'jaxa:darts')

# The display labels that were being stored instead, mapped to the value each one
# was meant to represent. The field's default was 'NASA', a label rather than a
# value, so any profile created without an explicit agency picked one up.
LABEL_TO_VALUE = {
    'NASA': 'nasa:pds',
    'ESA': 'esa:psa',
    'JAXA': 'jaxa:darts',
}


def forwards(apps, schema_editor):
    """Replace stored display labels with the choice values they stand for.

    Bundle.lid() interpolates this field straight into a bundle's logical
    identifier, so a profile holding 'NASA' produced urn:NASA:<bundle>. That fails
    PDS4 twice over: the pattern requires lowercase, and an archive product LID
    needs four segments where this has three.

    No Archive bundle currently belongs to an affected profile, so nothing on disk
    is wrong today. This is repaired so that it stays that way - the next Archive
    bundle one of these users creates would have carried an invalid LID into every
    label in it.
    """
    UserProfile = apps.get_model('friends', 'UserProfile')

    repaired = 0
    unrecognised = []

    for profile in UserProfile.objects.exclude(agency__in=VALID):
        replacement = LABEL_TO_VALUE.get((profile.agency or '').strip())
        if replacement is None:
            # Leave anything unrecognised alone rather than guessing at what a
            # user's agency was meant to be.
            unrecognised.append((profile.pk, profile.agency))
            continue
        profile.agency = replacement
        profile.save(update_fields=['agency'])
        repaired += 1

    print('  agency labels repaired: {}'.format(repaired))
    for pk, value in unrecognised:
        print('  profile {} left alone, unrecognised agency {!r}'.format(pk, value))


def backwards(apps, schema_editor):
    """No-op. The old values were invalid; restoring them would only reintroduce
    the bad LIDs, and there is no way to tell which rows originally held them."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('friends', '0007_alter_updateagency_agency_alter_userprofile_agency'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
