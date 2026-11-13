"""
Lets the citation people and label update tables hold any name.

New tables get the database default, latin1 on ELSA's MariaDB, which can't hold
names like Dvořák or Rafał. In strict mode the whole save fails, so saving a
citation, or moving an old one out of its labels, would fail for those people.

JSON columns keep the binary collation Django gave them. MariaDB and MySQL only.
Not reversed: going back to latin1 would lose the names.
"""
from django.db import migrations

TABLES = ('build_citation_contributor', 'build_bundleupgrade')


def to_utf8mb4(apps, schema_editor):
    db = schema_editor.connection
    if db.vendor != 'mysql':
        return
    quote = db.ops.quote_name
    with db.cursor() as cursor:
        for table in TABLES:
            cursor.execute(
                "SELECT COLUMN_NAME, IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = %s AND DATA_TYPE = 'longtext' AND COLLATION_NAME = 'utf8mb4_bin'", [table])
            json_columns = cursor.fetchall()
            cursor.execute('ALTER TABLE {0} CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci'.format(
                quote(table)))
            for column, nullable in json_columns:
                cursor.execute('ALTER TABLE {0} MODIFY {1} LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin {2} '
                               'CHECK (json_valid({1}))'.format(quote(table), quote(column),
                                                                'NULL' if nullable == 'YES' else 'NOT NULL'))


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0083_netcdf_upload'),
    ]

    operations = [
        migrations.RunPython(to_utf8mb4, migrations.RunPython.noop, elidable=False),
    ]
