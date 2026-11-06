"""Cache the PDS4 schemas ELSA uses and write an XML catalog for them.

    python3 manage.py build_schema_catalog
    python3 manage.py build_schema_catalog --force     # re-download cached files

Without this, validate fetches every schema from pds.nasa.gov on each run. validate uses
the catalog with its -C flag. The list comes from VERSION_CHOICES plus the AMA dictionary.
"""
import os

import requests
from lxml import etree
from django.core.management.base import BaseCommand, CommandError

from build.forms import VERSION_CHOICES
from build.validate_runner import work_dir

PDS_CORE_URL = 'https://pds.nasa.gov/pds4/pds/v1/PDS4_PDS_{version}.{extension}'

# External bundles use this dictionary. It's built against core 1O00, so they move together.
# See templates/pds4_labels/base_templates/Template_PE.xml.
AMA_LDD_URL = 'https://pds.nasa.gov/pds4/ama/v1/PDS4_AMA_1O00_1300.{extension}'

EXTENSIONS = ('xsd', 'sch')

# Root element each kind of file has to have. An error page can still parse as XML.
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

    Both `uri` and `system` entries, since schemaLocation and xml-model resolve differently.
    Both http and https, since our templates use both and matching is exact.
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
                # A version nobody uses may not be published. Report it and carry on.
                failed.append((url, str(error)))
                continue

            # Make sure the body is really the schema before caching. A proxy can answer 200 with an
            # error page, so check the root element too.
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

        # Only catalog what's on disk.
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
