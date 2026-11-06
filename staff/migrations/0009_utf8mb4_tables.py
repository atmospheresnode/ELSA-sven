"""
Stores any text people type in ELSA Admin.

The staff tables were created with the database's default character set,
which is latin1 on ELSA's MariaDB.  latin1 cannot hold Greek letters,
arrows, CJK or emoji, and MariaDB in strict mode refuses the whole write
(error 1366), so a reply saying "Δt = 5 s" was a server error.  This
converts every staff table to utf8mb4.

JSON columns (on MariaDB, LONGTEXT with a json_valid check) are put back to
the binary collation Django gave them, which CONVERT TO would change.

MariaDB and MySQL only; nothing to do on SQLite.  Not reversed: going back
to latin1 would destroy the text this exists to keep.
"""
from django.db import migrations

from staff.dbtext import to_utf8mb4


class Migration(migrations.Migration):

    dependencies = [
        ('staff', '0008_broadcast_background_sending'),
    ]

    operations = [
        migrations.RunPython(to_utf8mb4, migrations.RunPython.noop, elidable=False),
    ]
