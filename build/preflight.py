# -*- coding: utf-8 -*-
"""What ELSA requires that the PDS validator structurally cannot see.

The validation panel reported "passed" on an External bundle with no target. That
was not a bug in a rule; it was the panel answering a narrower question than the one
being asked of it. The PDS validator can only report on what is *in* a label, and
PDS4 makes both of these optional:

    Target_Identification    minOccurs="0"  in Context_Area
    Modification_History     minOccurs="0"  in Identification_Area

So a bundle with no target produces a label with no Target_Identification, which is
valid PDS4, and validate correctly says nothing about it. Meanwhile ELSA requires a
target before a bundle may be submitted: Bundle.get_status() will not return 'ready'
without one, and the Review & Submit button stays disabled. The bundle page said
"Targets: missing" in red and the validation panel said "passed", on the same screen,
about the same bundle.

The general shape of the gap, worth remembering before adding a rule to
validate_rules.py that will never fire: **anything ELSA requires that is expressed as
an absence is invisible to the validator.** Absence is not a finding. Those
requirements have to be checked here, against the database, and folded into the same
panel so a user reads one verdict rather than two.

These checks are free. They are three EXISTS queries, no JVM, no schema compilation,
so unlike a validation run they can be shown whenever the panel is open, including
before anything has been checked.

The first three are the ones ELSA has always enforced on the Review & Submit
button. Two more apply to External bundles only: at least one author on the citation
(a citation may omit List_Author entirely, and validate only asks for authors when a
DOI is present) and at least one processed NetCDF file (PDS4 accepts a bundle with
no data at all). Both were confirmed end to end: the real validator reports nothing
when either is missing.

This module is the single place they are written down. The validation panel, the
Review & Submit list, the submission gate and Bundle.get_status() all read them
from here, so none of them can call a bundle ready while another refuses it.
"""
from __future__ import unicode_literals

from django.db import models

from build.validate_rules import (CARD_CITATION, CARD_CONTEXT, CARD_MOD_HISTORY,
                                  CARD_NETCDF, USER)


class Requirement(object):
    """One thing ELSA requires, and how to tell whether a bundle has it.

    `key` is deliberately shared with the validate_rules rule that covers the same
    ground where one exists. Citation Information is reported by both: ELSA ships the
    citation skeleton in every label, so an unfilled citation reaches the validator
    as blank elements *and* is absent from the database. Matching keys is what lets
    the merge drop the duplicate instead of listing it twice.
    """

    def __init__(self, key, field, title, detail, card, check=None, bundle_types=None,
                 applies=None):
        self.key = key
        self.field = field
        self.title = title
        self.detail = detail
        self.card, self.anchor, self.scroll_to = card
        # A requirement that is more than "a row exists" supplies its own check.
        self.check = check
        # None means every bundle type. The External-only requirements are the ones
        # ELSA can already satisfy end to end; Archive gets them once its labels can
        # pass validation at all, since a gate must never block on something ELSA
        # itself cannot produce.
        self.bundle_types = bundle_types
        # Whether it is worth asking at all yet. An author is asked for only once a
        # citation exists: before that, "Add Citation Information" is the one thing
        # to do, and listing its parts separately would say the same thing twice.
        self.applies = applies

    def relevant(self, bundle):
        if self.bundle_types is not None and bundle.bundle_type not in self.bundle_types:
            return False
        return self.applies is None or self.applies(bundle)

    def satisfied(self, bundle):
        if not self.relevant(bundle):
            return True
        if self.check is not None:
            return self.check(bundle)
        return getattr(bundle, self.field).exists()

    def item(self):
        """The same shape validate_rules.translate() produces, so the panel template
        renders a requirement and a finding with one loop and no special case."""
        return {
            'key': self.key,
            'title': self.title,
            'detail': self.detail,
            'card': self.card,
            'anchor': self.anchor,
            'scroll_to': self.scroll_to,
            'audience': USER,
            'subject': '',
            'labels': [],
            'findings': [],
            # Marks the item as ELSA's own check rather than something PDS said, so
            # the raw output tab is not expected to contain a matching line and the
            # panel can say where the item came from.
            'source': 'elsa',
        }


def _has_citation(bundle):
    return bundle.citation_information_set.exists()


def _has_author(bundle):
    """At least one author, a person or an organization, declared on the citation.

    Counted from the database, where the citation keeps how many of each it has; the
    names themselves live only in the label. A citation declaring none writes no
    List_Author at all, which PDS4 allows, so validate says nothing. One declaring
    an author but leaving the name blank is a different case, and validate already
    reports it (citation-author-blank), so it is not counted twice here.
    """
    return bundle.citation_information_set.filter(
        models.Q(number_of_authors_people__gt=0)
        | models.Q(number_of_authors_organization__gt=0)).exists()


def _has_processed_netcdf(bundle):
    """At least one NetCDF file ELSA processed, which is what gives it a label.

    An upload ELSA could not read is not saved at all, but a file whose label later
    failed to regenerate stays behind with processed=False. It has no label and is
    not in any inventory, so it is not a product in the bundle, and cannot be the
    one that makes the bundle a delivery.
    """
    return bundle.netcdf_files.filter(processed=True).exists()


REQUIREMENTS = [
    Requirement(
        'citation-missing', 'citation_information_set',
        'Add Citation Information, including a description',
        'PDS needs to know who produced this data and what it is, so it can be '
        'cited and found later. The description is the part reviewers read first.',
        CARD_CITATION),

    Requirement(
        'modification-history-missing', 'modification_history_set',
        'Add a Modification History entry',
        'Every PDS4 bundle records what changed and when, starting with its first '
        'delivery. Add one entry describing this version, usually 1.0 with a '
        'description like "Initial delivery".',
        CARD_MOD_HISTORY),

    Requirement(
        'target-missing', 'targets',
        'Choose at least one target',
        'A target is what the data is about: the planet, moon or body observed or '
        'modelled. PDS4 lets a label leave it out, so the validation tool will not '
        'report this, but ELSA will not send a bundle for review without one. '
        'Open Context Products and pick one from the Targets tab.',
        CARD_CONTEXT),

    # External only, for now: see Requirement.bundle_types.
    Requirement(
        'author-missing', None,
        'Add at least one author to the citation',
        'A citation needs someone to credit: a person or an organization. PDS4 lets a '
        'citation leave its authors out, so the validation tool will not report '
        'this, but without one the data cannot be cited or given a DOI. The number '
        'of authors is set when a citation is created and cannot be changed after, '
        'so open Citation Information, delete this citation, and create it again '
        'with at least one author.',
        CARD_CITATION, check=_has_author, bundle_types=('External',),
        applies=_has_citation),

    Requirement(
        'netcdf-missing', None,
        'Upload at least one NetCDF file',
        'An AMA bundle delivers model output, and this one has none yet. PDS4 allows '
        'a bundle without data, so the validation tool will not report this. Add a '
        'collection in the Collections card if there is none, then upload your NetCDF '
        'files into it. A file counts once ELSA has processed it.',
        CARD_NETCDF, check=_has_processed_netcdf, bundle_types=('External',)),
]


def requirements(bundle):
    """The requirements this bundle has not met yet, in the order listed above."""
    return [r.item() for r in REQUIREMENTS if not r.satisfied(bundle)]


def status(bundle):
    """The Review & Submit button's view of the original three checks.

    Keys match the names the bundle page's status_dict has always used, so the
    template did not have to change when this moved here. Whether everything is met,
    including the requirements added since, is met().
    """
    return {
        'Citation_Information': REQUIREMENTS[0].satisfied(bundle),
        'Modification_History': REQUIREMENTS[1].satisfied(bundle),
        'Targets': REQUIREMENTS[2].satisfied(bundle),
    }


def met(bundle):
    """True when nothing ELSA requires is outstanding."""
    return not requirements(bundle)
