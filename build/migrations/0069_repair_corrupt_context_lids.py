import ast

from django.db import migrations


# Context models whose lid column was corrupted. All relations between these
# models are M2M, so merging a duplicate is a matter of repointing through-table
# rows and deleting the leftover.
CONTEXT_MODELS = [
    'Investigation',
    'Target',
    'Instrument_Host',
    'Instrument',
    'Facility',
    'Telescope',
]


def normalize_lid(lid):
    """Turn "['urn:nasa:pds:context:target:planet.mars']" into the bare LID.

    An older crawler run stored the repr of a one-element list instead of the
    string inside it. Those rows also never picked up any internal references,
    and their lid was being written verbatim into lid_reference elements in
    generated PDS4 labels, which is not a resolvable LID.

    Returns None when the value is not a recoverable single-element list, so
    the caller can leave the row alone rather than guess.
    """
    if not lid or not lid.startswith('['):
        return None
    try:
        value = ast.literal_eval(lid)
    except (ValueError, SyntaxError):
        return None
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], str):
        return value[0].strip() or None
    return None


def through_tables_for(model):
    """Every M2M through model that has `model` on one side.

    Both directions matter. related_objects covers the case where another model
    points at this one (Bundle.targets -> Target); many_to_many covers the case
    where this model owns the relation (Investigation.targets -> Target). If we
    only handled the first, deleting a duplicate Investigation would silently
    drop the targets hanging off it.
    """
    seen = {}
    for rel in model._meta.related_objects:
        if rel.field.many_to_many:
            through = rel.field.remote_field.through
            seen[through._meta.db_table] = through
    for field in model._meta.many_to_many:
        through = field.remote_field.through
        seen[through._meta.db_table] = through
    return list(seen.values())


def split_through_fks(through, model):
    """Return (fk_to_model, fk_to_other) for a through model."""
    fks = [
        f for f in through._meta.get_fields()
        if f.is_relation and f.many_to_one
    ]
    to_model = None
    other = None
    for f in fks:
        if to_model is None and f.related_model._meta.label_lower == model._meta.label_lower:
            to_model = f
        else:
            other = f
    return to_model, other


def merge(model, duplicate_pk, keeper_pk):
    """Move every relation off `duplicate_pk` onto `keeper_pk`, then delete it."""
    for through in through_tables_for(model):
        to_model, other = split_through_fks(through, model)
        if to_model is None or other is None:
            continue

        mine = '{}_id'.format(to_model.name)
        theirs = '{}_id'.format(other.name)

        for row in through.objects.filter(**{mine: duplicate_pk}):
            other_id = getattr(row, theirs)
            already = through.objects.filter(
                **{mine: keeper_pk, theirs: other_id}
            ).exists()
            if already:
                row.delete()
            else:
                setattr(row, mine, keeper_pk)
                row.save()

    model.objects.filter(pk=duplicate_pk).delete()


def forwards(apps, schema_editor):
    for model_name in CONTEXT_MODELS:
        model = apps.get_model('build', model_name)

        # Rows whose lid is already well formed win, oldest first.
        keepers = {}
        for pk, lid in model.objects.exclude(
                lid__startswith='[').values_list('pk', 'lid').order_by('pk'):
            if lid and lid not in keepers:
                keepers[lid] = pk

        repaired = 0
        merged = 0
        skipped = 0

        corrupt = list(
            model.objects.filter(lid__startswith='[')
            .values_list('pk', 'lid').order_by('pk')
        )

        for pk, lid in corrupt:
            clean = normalize_lid(lid)
            if clean is None:
                skipped += 1
                continue

            keeper_pk = keepers.get(clean)
            if keeper_pk is None:
                # No well-formed twin, so this row becomes the canonical one.
                model.objects.filter(pk=pk).update(lid=clean)
                keepers[clean] = pk
                repaired += 1
            else:
                merge(model, pk, keeper_pk)
                merged += 1

        print(
            '  {}: {} normalized in place, {} merged into an existing row, '
            '{} left alone (unparseable)'.format(
                model_name, repaired, merged, skipped)
        )


def backwards(apps, schema_editor):
    """No-op. Merged rows cannot be reconstructed; restore from backup."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0068_remove_target_investigations_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
