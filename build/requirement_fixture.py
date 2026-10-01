# -*- coding: utf-8 -*-
"""Give a test bundle the three things ELSA requires of every bundle.

Not named test_*.py on purpose: it holds no tests and should not be collected as a
module of them.

Most validation tests build `Bundle.objects.create(...)` and nothing else, then
assert on what the panel says or whether the gate opens. That was a faithful fixture
while the panel's verdict came only from the PDS validator. It is not one now:
build.preflight refuses a bundle with no citation, no modification history or no
target, so a bare row describes a bundle that can never be submitted, and a test
asserting "nothing blocks this" against one is asserting something production will
not do.

So a test about validation says so here, and gets a bundle whose only remaining
problems are the findings it puts there itself.
"""
from __future__ import unicode_literals

from build.models import (Citation_Information, Modification_History, NetCDFFile,
                          Target)


def satisfy_requirements(bundle, skip=(), target_name='Mars'):
    """Meet build.preflight's requirements. Returns the bundle.

    `skip` names requirement keys to leave outstanding, for a test that supplies one
    itself and would otherwise find two of them.
    """
    if 'citation-missing' not in skip and not bundle.citation_information_set.exists():
        Citation_Information.objects.create(
            bundle=bundle,
            description='A bundle used in a test.',
            publication_year='2026',
            number_of_authors_people=0 if 'author-missing' in skip else 1)

    if ('modification-history-missing' not in skip
            and not bundle.modification_history_set.exists()):
        Modification_History.objects.create(
            bundle=bundle,
            description='Initial delivery',
            modification_date='2026-09-18',
            version_id='1.0')

    if 'target-missing' not in skip and not bundle.targets.exists():
        target, _ = Target.objects.get_or_create(
            lid='urn:nasa:pds:context:target:planet.{}'.format(target_name.lower()),
            defaults={'name': target_name, 'type_of': 'Planet', 'file_ref': ''})
        bundle.targets.add(target)

    # The External-only requirements (build.preflight). An Archive bundle is left
    # alone, so a test can tell the two apart.
    if bundle.bundle_type == 'External':
        if 'author-missing' not in skip:
            citation = bundle.citation_information_set.first()
            if (citation is not None and not citation.number_of_authors_people
                    and not citation.number_of_authors_organization):
                citation.number_of_authors_people = 1
                citation.save(update_fields=['number_of_authors_people'])

        if ('netcdf-missing' not in skip
                and not bundle.netcdf_files.filter(processed=True).exists()):
            # A row, not a file: the requirement is about the database saying a
            # processed file exists. No collection either, which the model allows for
            # rows that predate collections: a collection row without its label on
            # disk breaks every view that writes into all of a bundle's collection
            # labels. Tests that need a real NetCDF upload one through the view.
            NetCDFFile.objects.create(
                bundle=bundle, title='fixture.nc', file='fixture.nc', processed=True)

    return bundle
