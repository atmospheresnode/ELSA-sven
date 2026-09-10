from django.db import migrations


# The Atmospheric Modeling Annex investigation as PDS actually registers it.
# Verified against the registered_context_products.json shipped with the PDS
# validate tool, and it is the value build/test_collection_types.py already
# asserts.
CORRECT_LID = 'urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex'

# What ELSA has been storing instead. Two things are wrong with it: the type
# segment should be 'individual', not 'individual_investigation', and the name
# segment separates words with underscores, not hyphens.
INCORRECT_LID = (
    'urn:nasa:pds:context:investigation:individual_investigation.atmospheric-modeling-annex'
)


def forwards(apps, schema_editor):
    """Point the AMA investigation at the LID PDS actually publishes.

    build/views.py attaches this row to every External bundle by name, so the
    wrong value was being written into the Investigation_Area of every External
    bundle and collection label ELSA has produced. validate reports it as
    error.label.context_ref_not_found: the label references a context product
    that does not exist.

    Existing bundles pick the correct value up the next time their labels are
    rebuilt. Labels already written to disk are deliberately not touched here -
    rewriting archived files, including bundles already submitted for review, is
    not something a migration should do quietly.
    """
    Investigation = apps.get_model('build', 'Investigation')

    wrong = list(Investigation.objects.filter(lid=INCORRECT_LID))
    if not wrong:
        print('  AMA investigation LID: nothing to repair.')
        return

    keeper = Investigation.objects.filter(lid=CORRECT_LID).order_by('pk').first()

    if keeper is None:
        # The ordinary case: no row already holds the correct LID, so the
        # existing row simply becomes correct and keeps its relations.
        updated = Investigation.objects.filter(lid=INCORRECT_LID).update(lid=CORRECT_LID)
        print('  AMA investigation LID: {} row(s) corrected in place.'.format(updated))
        return

    # A correctly-identified row already exists, so the bad rows are duplicates.
    # Move any bundles pointing at a duplicate over to the keeper before deleting
    # it, or those bundles would silently lose their investigation.
    BundleInvestigations = Investigation.bundle_set.through
    moved = 0
    for duplicate in wrong:
        if duplicate.pk == keeper.pk:
            continue
        for row in BundleInvestigations.objects.filter(investigation_id=duplicate.pk):
            already_linked = BundleInvestigations.objects.filter(
                investigation_id=keeper.pk, bundle_id=row.bundle_id
            ).exists()
            if already_linked:
                row.delete()
            else:
                row.investigation_id = keeper.pk
                row.save()
                moved += 1
        duplicate.delete()

    print(
        '  AMA investigation LID: merged {} duplicate(s) into row {}, '
        '{} bundle link(s) moved.'.format(len(wrong), keeper.pk, moved)
    )


def backwards(apps, schema_editor):
    """Put the incorrect LID back, so the migration can be reversed cleanly.

    Only meaningful when forwards took the in-place branch; a merge cannot be
    undone, and restoring the bad value would not bring a deleted row back.
    """
    Investigation = apps.get_model('build', 'Investigation')
    Investigation.objects.filter(lid=CORRECT_LID).update(lid=INCORRECT_LID)


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0074_alter_product_collection_collection'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
