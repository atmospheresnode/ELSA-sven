"""Cache the PDS4 schemas ELSA references and write an XML catalog pointing at them.

    python3 manage.py build_schema_catalog
    python3 manage.py build_schema_catalog --force     # re-download files already cached

The validate tool ships no schemas. Left to itself it fetches every schema and
schematron from pds.nasa.gov on each run, which puts the PDS website in the path of
every bundle submission: if it is slow or unreachable, validation is too. It also
means the same few files are downloaded over and over.

This caches them once and writes an OASIS XML catalog, which validate accepts with
its -C flag and uses to resolve those URLs locally.

Which schemas are needed comes from VERSION_CHOICES, so this stays correct when the
version list changes, plus the AMA local data dictionary that External bundles
reference. Both the .xsd and the .sch matter: the XSD carries structure and
datatypes, the schematron the value rules, and it is the schematron that holds most
of what a data provider actually gets wrong.
"""
import os

import requests
from lxml import etree
from django.core.management.base import BaseCommand, CommandError

from build.forms import VERSION_CHOICES
from build.validate_runner import work_dir

PDS_CORE_URL = 'https://pds.nasa.gov/pds4/pds/v1/PDS4_PDS_{version}.{extension}'

# External bundles reference this dictionary, and it is pinned: PDS4_AMA_1O00_1300
# is built against core build 1O00 and imports it directly, so the two move together.
# See the comment in templates/pds4_labels/base_templates/Template_PE.xml.
AMA_LDD_URL = 'https://pds.nasa.gov/pds4/ama/v1/PDS4_AMA_1O00_1300.{extension}'

EXTENSIONS = ('xsd', 'sch')

# The root element each kind of file must have. Checked on download, because a
# well-formed error page passes an XML parse perfectly happily.
EXPECTED_ROOTS = {
    'xsd': '{http://www.w3.org/2001/XMLSchema}schema',
    'sch': '{http://purl.oclc.org/dsdl/schematron}schema',
}


def catalog_path():
    return os.path.join(work_dir(), 'catalog.xml')


def schema_dir():
    return os.path.join(work_dir(), 'schemas')


def required_schemas():
    """Every schema URL ELSA's labels can reference, as {url: local filename}."""
    wanted = {}

    for version, _label in VERSION_CHOICES:
        for extension in EXTENSIONS:
            url = PDS_CORE_URL.format(version=version, extension=extension)
            wanted[url] = os.path.basename(url)

    for extension in EXTENSIONS:
        url = AMA_LDD_URL.format(extension=extension)
        wanted[url] = os.path.basename(url)

    return wanted


def write_catalog(entries, destination):
    """Write an OASIS catalog mapping each schema URL to its cached copy.

    Both `uri` and `system` entries are emitted for every file. Which one a resolver
    consults depends on how the reference is written: a schemaLocation attribute
    resolves as a system identifier, while an xml-model href resolves as a plain URI.
    Emitting one and not the other silently half-works, which is worse than not
    caching at all because the failure only shows up on some labels.

    http and https are both listed for the same reason. ELSA's own templates are not
    consistent about the scheme, and a catalog match is an exact string match.
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<catalog xmlns="urn:oasis:names:tc:entity:xmlns:xml:catalog">']

    for url, filename in sorted(entries.items()):
        local = os.path.join('schemas', filename).replace(os.sep, '/')
        for variant in (url, url.replace('https://', 'http://', 1)):
            lines.append('  <uri name="{}" uri="{}"/>'.format(variant, local))
            lines.append('  <system systemId="{}" uri="{}"/>'.format(variant, local))

    lines.append('</catalog>')
    lines.append('')

    with open(destination, 'w', encoding='utf-8') as catalog:
        catalog.write('\n'.join(lines))


class Command(BaseCommand):
    help = 'Cache the PDS4 schemas ELSA references and write an XML catalog for validate.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='Re-download schemas that are already cached.')

    def handle(self, *args, **options):
        target = schema_dir()
        os.makedirs(target, exist_ok=True)

        wanted = required_schemas()
        downloaded = 0
        cached = 0
        failed = []

        for url, filename in sorted(wanted.items()):
            destination = os.path.join(target, filename)

            if os.path.exists(destination) and not options['force']:
                cached += 1
                continue

            try:
                response = requests.get(url, timeout=60)
                response.raise_for_status()
            except requests.RequestException as error:
                # A schema for a version nobody uses may simply not be published.
                # Report it and carry on rather than failing the whole catalog.
                failed.append((url, str(error)))
                continue

            # Check the body really is the schema before caching it. A proxy or
            # captive portal can answer 200 with an error page, and a truncated body
            # also arrives looking like success. Either would sit in the cache being
            # served to validate, which then fails in ways that appear to be about
            # the label rather than about the schema behind it.
            #
            # Parsing is not enough on its own: an HTML error page is frequently
            # well-formed XML and sails through. The root element is what actually
            # distinguishes a schema from a polite apology.
            try:
                root = etree.fromstring(response.content)
            except etree.XMLSyntaxError as error:
                failed.append((url, 'response was not XML ({})'.format(error)))
                continue

            expected = EXPECTED_ROOTS[filename.rsplit('.', 1)[-1]]
            if root.tag != expected:
                failed.append((url, 'expected a {} document, got <{}>'.format(
                    filename.rsplit('.', 1)[-1], etree.QName(root).localname
                    if isinstance(root.tag, str) else root.tag)))
                continue

            with open(destination, 'wb') as schema:
                schema.write(response.content)
            downloaded += 1
            self.stdout.write('  downloaded {}'.format(filename))

        # Only catalog what is actually on disk, or validate would resolve a URL to a
        # file that is not there and fail in a way that points at the label instead.
        present = {url: filename for url, filename in wanted.items()
                   if os.path.exists(os.path.join(target, filename))}
        write_catalog(present, catalog_path())

        self.stdout.write('')
        self.stdout.write('  {} downloaded, {} already cached, {} catalogued'.format(
            downloaded, cached, len(present)))

        for url, error in failed:
            self.stdout.write(self.style.WARNING(
                '  could not fetch {}: {}'.format(url, error)))

        if not present:
            raise CommandError(
                'No schemas could be cached, so the catalog is empty. Check network '
                'access to pds.nasa.gov from this host.')

        self.stdout.write(self.style.SUCCESS(
            '  catalog written to {}'.format(catalog_path())))
