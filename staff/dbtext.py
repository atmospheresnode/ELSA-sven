"""Text handling for ELSA's MariaDB.

The database defaults to latin1 and the connection is utf8mb3, so Greek, CJK and emoji can
fail the whole save (error 1366).

- to_utf8mb4: converts staff_* tables. Run from a migration after creating a staff table.
- unstorable: finds text the database can't keep so a form can say so.
"""
from django.db import connection

CHARSET = 'utf8mb4'
COLLATION = 'utf8mb4_general_ci'


def to_utf8mb4(apps, schema_editor):
    db = schema_editor.connection
    if db.vendor != 'mysql':
        return
    quote = db.ops.quote_name
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME LIKE 'staff\\_%%' AND TABLE_COLLATION NOT LIKE 'utf8mb4%%'")
        for (table,) in cursor.fetchall():
            # JSON columns keep their binary collation, which CONVERT TO would change.
            cursor.execute(
                "SELECT COLUMN_NAME, IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = %s AND DATA_TYPE = 'longtext' AND COLLATION_NAME = 'utf8mb4_bin'", [table])
            json_columns = cursor.fetchall()
            cursor.execute('ALTER TABLE {0} CONVERT TO CHARACTER SET {1} COLLATE {2}'.format(
                quote(table), CHARSET, COLLATION))
            for column, nullable in json_columns:
                cursor.execute('ALTER TABLE {0} MODIFY {1} LONGTEXT CHARACTER SET {2} COLLATE utf8mb4_bin {3} '
                               'CHECK (json_valid({1}))'.format(quote(table), quote(column), CHARSET,
                                                                'NULL' if nullable == 'YES' else 'NOT NULL'))


def _connection_charset():
    if connection.vendor != 'mysql':
        return CHARSET
    with connection.cursor() as cursor:
        cursor.execute('SELECT @@character_set_connection')
        return cursor.fetchone()[0]


def unstorable(*texts):
    """Characters in ``texts`` this connection can't carry, as a short string, or ''."""
    wide = sorted({ch for text in texts for ch in (text or '') if ord(ch) > 0xFFFF})
    if not wide or _connection_charset() == CHARSET:
        return ''
    return ' '.join(wide[:5])


def narrow(text):
    """Replace what this connection can't carry with U+FFFD, for text nobody can retype."""
    if not text or not any(ord(ch) > 0xFFFF for ch in text) or _connection_charset() == CHARSET:
        return text
    return ''.join(ch if ord(ch) <= 0xFFFF else '\ufffd' for ch in text)
