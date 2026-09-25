"""Report where the live database disagrees with the models.

    python3 manage.py check_schema_drift

No test can find this. The test database is built from the migration files, so it
matches the models by construction; drift exists only in a database that has been
restored, patched by hand, or had a migration fail halfway. It is invisible until
someone touches the one code path that reads or writes the affected column, and
then it is a 500 with no obvious cause.

Two directions, both of which have bitten this project:

* A column in the database that the models do not have. Harmless while it is
  nullable or has a default; fatal otherwise, because Django omits it from every
  INSERT and the database refuses the row. build_product_document.doi was exactly
  this, and it made it impossible to add a document to any bundle.
* A field in the models that the database does not have. Every read of that table
  fails. A migration that was committed but never applied does this, which is what
  ValidationRun.content_fingerprint did for an hour.

Exits non-zero when something would break, so it can gate a deploy.
"""
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Report columns where the live database and the models disagree.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--app', default=None,
            help='Only this app label, rather than every installed app.')

    def handle(self, *args, **options):
        if connection.vendor != 'mysql':
            self.stdout.write(
                'Only implemented for MySQL/MariaDB; nothing checked.')
            return

        models = apps.get_models()
        if options['app']:
            models = [m for m in models if m._meta.app_label == options['app']]

        breaking, harmless, missing_tables = [], [], []

        with connection.cursor() as cursor:
            for model in models:
                table = model._meta.db_table
                cursor.execute(
                    'SELECT COUNT(*) FROM information_schema.TABLES '
                    'WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', [table])
                if not cursor.fetchone()[0]:
                    missing_tables.append((table, model.__name__))
                    continue

                cursor.execute(
                    'SELECT COLUMN_NAME, IS_NULLABLE, COLUMN_DEFAULT '
                    'FROM information_schema.COLUMNS '
                    'WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', [table])
                db_columns = {name: (nullable, default)
                              for name, nullable, default in cursor.fetchall()}
                model_columns = {f.column for f in model._meta.fields}

                for column in model_columns - set(db_columns):
                    breaking.append((table, column, 'in the model, not in the database'))

                for column in set(db_columns) - model_columns:
                    nullable, default = db_columns[column]
                    if nullable == 'NO' and default is None:
                        breaking.append((
                            table, column,
                            'in the database as NOT NULL with no default, not in '
                            'the model: every INSERT into this table fails'))
                    else:
                        harmless.append((table, column))

        for table, name in missing_tables:
            self.stdout.write(self.style.WARNING(
                '  table missing: {} ({})'.format(table, name)))

        for table, column, why in breaking:
            self.stdout.write(self.style.ERROR(
                '  {}.{}: {}'.format(table, column, why)))

        for table, column in harmless:
            self.stdout.write(
                '  {}.{}: in the database but not the model, and nullable, so '
                'nothing breaks'.format(table, column))

        self.stdout.write('')
        if breaking:
            self.stdout.write(self.style.ERROR(
                '{} column(s) would break at runtime.'.format(len(breaking))))
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(
            'No drift that would break anything ({} tables checked).'.format(
                len(models) - len(missing_tables))))
