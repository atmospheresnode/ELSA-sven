# -*- coding: utf-8 -*-
"""The bundle shapes the corpus covers.

Each is a situation a real user reaches. Some are deliberately incomplete: a
harness that only builds finished bundles cannot tell a correct complaint from a
missing rule, and "what does ELSA say to someone who has not finished yet" is the
question this feature exists to answer well.

`expect_user` is what a person should be told to do, by rule key. It is the
assertion that matters: not how many findings PDS produced, but whether what
reaches the user is the right short list.
"""
from __future__ import unicode_literals

import os

# A real EPIC model output file, the kind an AMA delivery actually contains. Kept
# outside the repository because it is 5MB of someone else's data; the shapes that
# need it are skipped when it is absent rather than failing, so the corpus still
# runs on a machine that does not have it.
NETCDF_FIXTURE = '/home/rupakdey/elsa/test-fixtures/epic000.nc'


def have_netcdf():
    return os.path.exists(NETCDF_FIXTURE)


SHAPES = [
    {
        'name': 'archive bare',
        'why': 'An Archive bundle just created and nothing else done.',
        'build': lambda b: b.create('corpus archive bare', 'Archive'),
        'expect_user': {'citation-missing', 'collection-empty',
                        'document-collection-empty'},
    },
    {
        'name': 'archive with citation',
        'why': 'The citation filled in, collections still empty.',
        'build': lambda b: (b.create('corpus archive cited', 'Archive')
                            .add_citation().fill_authors()
                            .add_modification_history()),
        'expect_user': {'collection-empty', 'document-collection-empty'},
    },
    {
        'name': 'archive complete',
        'why': 'Everything a user can do on the Archive side.',
        'build': lambda b: (b.create('corpus archive full', 'Archive')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_document('user_guide')),
        'expect_user': {'collection-empty'},
    },
    {
        'name': 'external bare',
        'why': 'An AMA bundle just created.',
        'build': lambda b: b.create('corpus external bare', 'External'),
        'expect_user': {'citation-missing', 'document-collection-empty'},
    },
    {
        'name': 'external with collection',
        'why': 'A user-added collection, still empty.',
        'build': lambda b: (b.create('corpus external coll', 'External')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_collection('sims')),
        'expect_user': {'collection-empty', 'document-collection-empty'},
    },
    {
        'name': 'external complete',
        'why': 'An AMA bundle with a document and a data collection.',
        'build': lambda b: (b.create('corpus external full', 'External')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_collection('sims')
                            .add_document('user_guide')),
        'expect_user': {'collection-empty'},
    },
    # 'expect_user' throughout this file means "what the PDS validator reports",
    # which is what this corpus exists to measure. It is not everything the panel
    # shows. ELSA's own requirements (build/preflight.py) are checked against the
    # database, never against a label, so a shape with no target expects nothing here
    # while the panel still asks the user for one. Those are covered in
    # build/test_preflight_requirements.py.
    {
        'name': 'external with target',
        'why': 'Selecting a target used to add errors to every label.',
        'build': lambda b: (b.create('corpus external target', 'External')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_document('user_guide')
                            .add_target()),
        'expect_user': set(),
    },
    {
        'name': 'ama with data',
        'why': 'The delivery an AMA user actually makes: model output plus a guide.',
        'needs_netcdf': True,
        'build': lambda b: (b.create('corpus ama data', 'External')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_collection('sims')
                            .add_document('user_guide')
                            .upload_netcdf(NETCDF_FIXTURE, 'sims')),
        'expect_user': set(),
    },
    {
        'name': 'ama with data and metadata',
        'why': 'The same, with the AMA dictionary content filled in.',
        'needs_netcdf': True,
        'build': lambda b: (b.create('corpus ama meta', 'External')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_collection('sims')
                            .add_document('user_guide')
                            .upload_netcdf(NETCDF_FIXTURE, 'sims')
                            .fill_ama()),
        'expect_user': set(),
    },
    {
        'name': 'two documents',
        'why': 'Distinct names; a collection listing one identifier twice is invalid.',
        'build': lambda b: (b.create('corpus two docs', 'External')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_document('user_guide')
                            .add_document('variable_reference')),
        'expect_user': set(),
    },
    {
        'name': 'ama metadata with a value the dictionary rejects',
        'why': ('Proves the AMA schematron is actually running and that what it '
                'reports reaches the user in plain language. A rule nobody has '
                'watched fire is a rule nobody knows works.'),
        'needs_netcdf': True,
        'build': lambda b: (b.create('corpus ama bad value', 'External')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_collection('sims')
                            .add_document('user_guide')
                            .upload_netcdf(NETCDF_FIXTURE, 'sims')
                            .fill_ama()
                            .corrupt_ama_value()),
        'expect_user': {'ama-vocabulary'},
    },
    {
        'name': 'with an alias',
        'why': 'Alias_List is optional and was one of the empty stubs ELSA shipped.',
        'build': lambda b: (b.create('corpus alias', 'External')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_document('user_guide')
                            .add_alias()),
        'expect_user': set(),
    },

    # -- input a real user has actually produced ---------------------------------
    {
        'name': 'unicode in the description',
        'why': ('Scientists write degrees and microns. PDS accepts those in a '
                'description, which is UTF-8, and the corpus pins that it stays '
                'accepted.'),
        'build': lambda b: (b.create('corpus unicode', 'External')
                            .add_citation(
                                description=(
                                    'Zonal winds at 5\u00b0 resolution, '
                                    '\u00b11 \u03bcm bands'))
                            .fill_authors()
                            .add_modification_history()
                            .add_document('user_guide')),
        'expect_user': set(),
    },
    {
        'name': 'an accented author name',
        'why': ('PDS holds names in ASCII_* types, all Basic Latin only, so '
                '"Ra\u00fal Morales-Juber\u00edas" is refused however correctly it '
                'is spelled. The form now says so and suggests the spelling that '
                'works, so what reaches the label is blank rather than invalid, and '
                'the panel asks for the name rather than reporting a pattern.'),
        'build': lambda b: (b.create('corpus accented name', 'External')
                            .add_citation().fill_authors(
                                given='Ra\u00fal', family='Morales-Juber\u00edas')
                            .add_modification_history()
                            .add_document('user_guide')),
        'expect_user': {'citation-author-blank'},
    },
    {
        'name': 'a long bundle name',
        'why': 'LIDs have a length limit and are built from the bundle name.',
        'build': lambda b: (b.create('corpus ' + 'long ' * 8 + 'name', 'External')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_document('user_guide')),
        'expect_user': set(),
    },
    {
        'name': 'an ampersand in the description',
        'why': 'XML has five characters that cannot be written literally.',
        'build': lambda b: (b.create('corpus escaping', 'External')
                            .add_citation(
                                description='Winds & temperatures <2 bar, "mid" range')
                            .fill_authors(given="O'Brien", family='Smith & Sons')
                            .add_modification_history()
                            .add_document('user_guide')),
        'expect_user': set(),
    },
    {
        'name': 'many documents',
        'why': 'The inventory has to list every one of them, exactly once.',
        'build': lambda b: (b.create('corpus many docs', 'External')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_document('guide_one').add_document('guide_two')
                            .add_document('guide_three').add_document('guide_four')
                            .add_document('guide_five')),
        'expect_user': set(),
    },
    {
        'name': 'netcdf with an invisible character in its name',
        'why': ('Raul\'s real delivery: eight files carrying U+F022, which renders '
                'as nothing and is illegal in a PDS4 file name.'),
        'needs_netcdf': True,
        'build': lambda b: (b.create('corpus pua name', 'External')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_collection('sims')
                            .add_document('user_guide')
                            .upload_netcdf(NETCDF_FIXTURE, 'sims',
                                           as_name='epic00000-00\uf02208\uf02220.nc')),
        'expect_user': set(),
    },
    {
        'name': 'document named without an extension',
        'why': ('The user types "guide" and picks PDF/A. ELSA completes it to '
                'guide.pdf rather than refusing, because the format field has '
                'already said what the file is.'),
        'build': lambda b: (b.create('corpus bare doc name', 'External')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_document('guide', file_name='noextension')),
        'expect_user': set(),
    },
    {
        'name': 'document with a space in its file name',
        'why': ('A name ELSA cannot complete or repair. The form refuses it, so no '
                'document is created and the collection stays empty, which is what '
                'the panel should then say.'),
        'build': lambda b: (b.create('corpus spaced doc name', 'External')
                            .add_citation().fill_authors()
                            .add_modification_history()
                            .add_document('guide', file_name='my guide.pdf')),
        'expect_user': {'document-collection-empty'},
    },
]
