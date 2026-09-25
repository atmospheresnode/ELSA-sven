"""Make the orphan build_product_document.doi column harmless.

Migration 0008 removed `doi` from Product_Document in 2022 and is recorded as
applied here, but the column is present in this database again, NOT NULL with no
default, holding nine junk values written after that date. Its two companions from
the same RemoveField, acknowledgement_text and editor_list, are correctly gone, so
something re-created this one alone: a restore from an older dump is the likeliest
explanation.

Django does not know the column exists, so it omits it from every INSERT, and MySQL
refuses the row: "Field 'doi' doesn't have a default value". That made it impossible
to add a document to any bundle, which is the one thing standing between an AMA
bundle and a clean validation.

This does not drop the column. The values are unreachable from the application and
look like test input, but dropping data is not a decision to take inside a bug fix,
and a nullable column with a default is enough to unbreak the insert. Dropping it is
a separate, deliberate step if the node wants the table tidy.

Guarded, so it is a no-op on any database built from migrations: there the column
does not exist, which includes the test database and a fresh production install.
"""
from django.db import migrations


COLUMN_EXISTS = """
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'build_product_document'
      AND COLUMN_NAME = 'doi'
"""


def repair(apps, schema_editor):
    # Superseded, so it does nothing. main's 0075_product_document_doi brought the
    # column back into the model as a real field (CharField, default ''), and it is
    # already applied on production. Altering that column here would change a field
    # Django now manages. The migration is kept, empty, because databases that ran it
    # before the merge have it recorded; 0079 joins the two histories.
    return


def undo(apps, schema_editor):
    # Deliberately nothing. Putting the column back to NOT NULL without a default
    # would restore the bug, and there is nothing else to restore.
    return


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0077_validationrun_content_fingerprint'),
    ]

    operations = [
        migrations.RunPython(repair, undo),
    ]
