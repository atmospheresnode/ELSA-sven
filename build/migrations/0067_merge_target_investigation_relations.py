from django.db import migrations


def forwards(apps, schema_editor):
    """Fold Target.investigations into Investigation.targets.

    Historically there were two independent M2M tables between Investigation
    and Target:

        build_investigation_targets  (Investigation.targets)  - read by the
            investigation-filtered target dropdown in TargetForm
        build_target_investigations  (Target.investigations)  - written by
            context_search_target_inv when a user picks a target under an
            investigation

    Nothing ever copied rows between them, so a mission association made by a
    user never showed up in that mission's target list. Investigation.targets
    is the surviving relation; this moves the orphaned rows over.
    """
    Investigation = apps.get_model('build', 'Investigation')
    Target = apps.get_model('build', 'Target')

    ThroughOld = Target.investigations.through
    ThroughNew = Investigation.targets.through

    existing = set(
        ThroughNew.objects.values_list('investigation_id', 'target_id')
    )

    to_create = [
        ThroughNew(investigation_id=investigation_id, target_id=target_id)
        for target_id, investigation_id in ThroughOld.objects.values_list(
            'target_id', 'investigation_id')
        if (investigation_id, target_id) not in existing
    ]

    ThroughNew.objects.bulk_create(to_create, ignore_conflicts=True)


def backwards(apps, schema_editor):
    """No-op.

    The merge is not reversible: once folded together we cannot tell which
    rows originated in build_target_investigations. Reversing the schema
    migration that follows will recreate an empty table, which is the same
    state a fresh install would have had.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0066_citation_information_number_of_editors_organization_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
