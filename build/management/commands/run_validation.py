"""Execute one queued validation run.

    python3 manage.py run_validation <run_id>

This is the unit of work. A view creates a ValidationRun row and launches this as a
detached child, then returns immediately; the command does the waiting and records
the result on the row it was given, so nothing in the web process is holding a
handle to a subprocess that outlives the request.

Runnable by hand, which is the point of it being a command rather than a thread: a
stuck or failed run can be re-executed and watched directly.
"""
from django.core.management.base import BaseCommand, CommandError

from build.models import ValidationRun
from build.validate_runner import run


class Command(BaseCommand):
    help = 'Run the PDS validate tool for one ValidationRun and record the result.'

    def add_arguments(self, parser):
        parser.add_argument('run_id', type=int, help='ValidationRun primary key.')

    def handle(self, *args, **options):
        run_id = options['run_id']

        try:
            validation_run = ValidationRun.objects.get(pk=run_id)
        except ValidationRun.DoesNotExist:
            raise CommandError('No ValidationRun with id {}.'.format(run_id))

        self.stdout.write('Validating {} ({} tier)'.format(
            validation_run.bundle.name, validation_run.tier))

        validation_run = run(run_id)

        if validation_run.status == ValidationRun.STATUS_FAILED:
            # Written to the row as well, so the UI can say what went wrong rather
            # than showing a run that simply never finished.
            self.stdout.write(self.style.ERROR(
                '  failed: {}'.format(validation_run.failure_reason)))
            return

        duration = validation_run.duration_seconds()
        self.stdout.write(self.style.SUCCESS(
            '  {} errors, {} warnings across {} labels in {:.1f}s'.format(
                validation_run.error_count, validation_run.warning_count,
                validation_run.products_total, duration or 0.0)))
