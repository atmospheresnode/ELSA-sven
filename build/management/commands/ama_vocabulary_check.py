"""Report AMA values that the PDS4 Schematron will reject.

    python3 manage.py ama_vocabulary_check
    python3 manage.py ama_vocabulary_check --fix
    python3 manage.py ama_vocabulary_check --strict     # non-zero exit if anything is found

Three AMA attributes are closed vocabularies, and the constraint lives only in the dictionary's
Schematron - PDS4_AMA_1O00_1300.xsd carries no xs:enumeration at all. Until these fields became
selects they were free-text boxes whose placeholders suggested values the Schematron rejects
("General Circulation Model" for a field that wants GCM, "lat/lon" for one that wants lat-lon,
"sigma" for one that wants Sigma), so rows written before that change may hold values that pass
XSD validation and fail at submission.

The forms now flag such a value as "... (not a valid PDS4 value)" when the panel is next opened,
but nobody sees that until they open it. This command says how far the problem spreads without
waiting for someone to look.

--fix applies ONLY unambiguous normalisations: a value that matches an allowed one once case and
separators are ignored ("lat/lon" -> "lat-lon", "sigma" -> "Sigma"). Anything needing a judgement
call ("General Circulation Model" -> GCM) is reported with a suggestion and left alone, because
guessing at a user's meaning is not this command's job. Labels for affected files are rebuilt
afterwards, since a fixed database and a stale label on disk is worse than either alone.
"""
from django.core.management.base import BaseCommand, CommandError

from build.forms import AMA_ENUMERATIONS
from build.models import ModelMetadata, NetCDFFile, SimulationConfiguration
from build.views import regenerate_netcdf_labels

# (model class, attribute) pairs the Schematron constrains. FileDescription has none.
ENUMERATED_ATTRIBUTES = (
    (ModelMetadata, 'type'),
    (SimulationConfiguration, 'horizontal_grid_type'),
    (SimulationConfiguration, 'vertical_grid_type'),
)

# Values the LDD's own documentation suggests but its Schematron refuses. Reported as hints only;
# each is a change of meaning rather than of spelling, so a person decides.
SUGGESTIONS = {
    'general circulation model': 'GCM',
    'mesoscale model': 'MESOSCALE',
    'data assimilation': 'ASSIMILATION',
}


def _normalise(value):
    """Case and separator folded, so "lat/lon", "Lat Lon" and "lat-lon" all compare equal."""
    folded = value.strip().lower()
    for separator in ('/', ' ', '_'):
        folded = folded.replace(separator, '-')
    while '--' in folded:
        folded = folded.replace('--', '-')
    return folded


def unambiguous_replacement(value, allowed):
    """The one allowed value this differs from only by case or separator, or None."""
    matches = [option for option in allowed if _normalise(option) == _normalise(value)]
    return matches[0] if len(matches) == 1 else None


def find_violations():
    """Every stored value the Schematron would reject, grouped readably by bundle."""
    found = []
    for model_class, attribute in ENUMERATED_ATTRIBUTES:
        allowed = AMA_ENUMERATIONS[attribute]
        queryset = (model_class.objects
                    .exclude(**{attribute: ''})
                    .select_related('bundle', 'collection', 'netcdf_file'))
        for record in queryset:
            value = (getattr(record, attribute) or '').strip()
            if not value or value in allowed:
                continue
            found.append({
                'bundle': record.bundle,
                'collection': record.collection,
                'netcdf_file': record.netcdf_file,
                'model_class': model_class,
                'attribute': attribute,
                'value': value,
                'allowed': allowed,
                'replacement': unambiguous_replacement(value, allowed),
                'suggestion': SUGGESTIONS.get(value.strip().lower()),
                'record': record,
            })

    found.sort(key=lambda row: (
        getattr(row['bundle'], 'name', ''), row['attribute'], row['value']))
    return found


class Command(BaseCommand):
    help = 'Report AMA values that the PDS4 Schematron will reject, and optionally normalise them.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix', action='store_true',
            help='Apply unambiguous normalisations and rebuild the affected labels.')
        parser.add_argument(
            '--strict', action='store_true',
            help='Exit non-zero when anything non-conforming remains. For CI.')

    def handle(self, *args, **options):
        violations = find_violations()

        if not violations:
            self.stdout.write('No non-conforming AMA values found.')
            return

        self.stdout.write('{} non-conforming AMA value(s) found.\n'.format(len(violations)))

        current_bundle = None
        for row in violations:
            if row['bundle'] != current_bundle:
                current_bundle = row['bundle']
                owner = getattr(getattr(current_bundle, 'user', None), 'username', '?')
                self.stdout.write('\n  {} (owner: {})'.format(
                    getattr(current_bundle, 'name', '?'), owner))

            if row['netcdf_file'] is not None:
                scope = 'file {}'.format(row['netcdf_file'].title)
            else:
                scope = 'collection default'
            collection_name = getattr(row['collection'], 'collection_name', 'no collection')

            self.stdout.write('    {}.{} = {!r}'.format(
                row['model_class'].__name__, row['attribute'], row['value']))
            self.stdout.write('      in {} / {}'.format(collection_name, scope))
            self.stdout.write('      allowed: {}'.format(', '.join(row['allowed'])))
            if row['replacement']:
                self.stdout.write('      -> {!r} (spelling only{})'.format(
                    row['replacement'], ', applied' if options['fix'] else ', use --fix'))
            elif row['suggestion']:
                self.stdout.write('      -> possibly {!r}: suggested, NOT applied by --fix'.format(
                    row['suggestion']))
            else:
                self.stdout.write('      -> no automatic match; needs a person')

        fixed = self.apply_fixes(violations) if options['fix'] else 0

        remaining = len(violations) - fixed
        self.stdout.write('\n{} fixed, {} still non-conforming.'.format(fixed, remaining))
        if remaining and not options['fix']:
            self.stdout.write('Re-run with --fix to apply the spelling-only corrections.')

        if options['strict'] and remaining:
            raise CommandError(
                '{} AMA value(s) would fail PDS4 Schematron validation.'.format(remaining))

    def apply_fixes(self, violations):
        """Normalise what can be normalised, then rebuild the labels that depended on it."""
        fixed = 0
        touched_files = set()

        for row in violations:
            if not row['replacement']:
                continue
            record = row['record']
            setattr(record, row['attribute'], row['replacement'])
            record.save(update_fields=[row['attribute']])
            fixed += 1

            # A collection default reaches every file in that collection, so all of their labels
            # are now stale, not just the one row that changed.
            if record.netcdf_file_id is not None:
                touched_files.add(record.netcdf_file_id)
            elif record.collection_id is not None:
                touched_files.update(
                    NetCDFFile.objects.filter(collection_id=record.collection_id)
                    .values_list('id', flat=True))

        if not touched_files:
            return fixed

        self.stdout.write('\nRebuilding {} label(s)...'.format(len(touched_files)))
        by_bundle = {}
        for netcdf_file in NetCDFFile.objects.filter(id__in=touched_files):
            by_bundle.setdefault(netcdf_file.bundle_id, []).append(netcdf_file)

        for netcdf_files in by_bundle.values():
            errors = regenerate_netcdf_labels(netcdf_files[0].bundle, netcdf_files)
            for error in errors or []:
                self.stderr.write('  label rebuild failed: {}'.format(error))

        return fixed
