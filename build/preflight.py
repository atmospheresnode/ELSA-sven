# -*- coding: utf-8 -*-
"""What ELSA requires that the PDS validator can't see.

PDS4 makes these optional, so validate says nothing when they're missing:

    Target_Identification    minOccurs="0"  in Context_Area
    Modification_History     minOccurs="0"  in Identification_Area

Anything ELSA requires that shows up as an absence has to be checked here, against the database.

All requirements live here. The validation panel, Review & Submit, the submission gate and
Bundle.get_status() all read them.
"""
from __future__ import unicode_literals

from django.db import models

from build.validate_rules import (CARD_CITATION, CARD_CONTEXT, CARD_DOCUMENT, CARD_MOD_HISTORY,
                                  CARD_NETCDF, CARD_SUPPORT, USER)


class Requirement(object):
    """One thing ELSA requires and how to check it.

    `key` matches the validate_rules rule for the same thing, so the merge can drop the duplicate.
    """

    def __init__(self, key, field, title, detail, card, check=None, bundle_types=None,
                 applies=None, source='elsa'):
        self.key = key
        self.field = field
        self.title = title
        self.detail = detail
        self.card, self.anchor, self.scroll_to = card
        # A requirement that is more than "a row exists" supplies its own check.
        self.check = check
        # None means every bundle type. External-only for now since Archive can't pass validation yet.
        self.bundle_types = bundle_types
        # Whether to ask yet. An author is only asked for once there's a citation.
        self.applies = applies
        # 'elsa' for what ELSA requires of the owner, 'support' for what the team has to do.
        self.source = source

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
        """Same shape as validate_rules.translate() so the template uses one loop."""
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
            # Marks this as ELSA's own check, not something PDS said.
            'source': self.source,
        }


def _has_citation(bundle):
    return bundle.citation_information_set.exists()


def _has_author(bundle):
    """At least one author, a person or organization, counted from the database.

    A blank name is reported by validate (citation-author-blank), so it isn't counted here.
    """
    return bundle.citation_information_set.filter(
        models.Q(number_of_authors_people__gt=0)
        | models.Q(number_of_authors_organization__gt=0)).exists()


def _has_processed_netcdf(bundle):
    """At least one NetCDF file ELSA processed, which is what gives it a label."""
    return bundle.netcdf_files.filter(processed=True).exists()


def _has_document(bundle):
    """At least one document. Checked here so it shows up before validate has run."""
    return bundle.product_document_set.exists()


def _labels_up_to_date(bundle):
    """No label update has failed on this bundle since it last went through."""
    from build import label_upgrades
    return label_upgrades.failed_update(bundle) is None


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
        'this, but without one the data cannot be cited or given a DOI. Open '
        'Citation Information and add a person or an organization; authors can be '
        'added or removed there at any time.',
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

    # Shares its key with the validate_rules rule, so the two are listed once.
    Requirement(
        'document-collection-empty', None,
        'Add at least one document',
        'Every bundle gets a document collection, and PDS will not accept one that is '
        'empty. Add a document describing the data: how it was produced, what the '
        'variables mean, or how to read it. A README or user guide is enough.',
        CARD_DOCUMENT, check=_has_document, bundle_types=('External',)),

    # Otherwise a bundle could go for review with the very labels the update was meant to fix.
    Requirement(
        'label-update-failed', None,
        "Wait for the ELSA team to finish updating this bundle's labels",
        'ELSA tried to bring the labels in this bundle up to date with how it writes them now, '
        'and could not finish. Nothing was changed, and the ELSA team has been told. You can '
        'keep working on the bundle. It can be submitted once the update has gone through.',
        CARD_SUPPORT, check=_labels_up_to_date, source='support'),
]


def requirements(bundle):
    """The requirements this bundle has not met yet, in the order listed above."""
    return [r.item() for r in REQUIREMENTS if not r.satisfied(bundle)]


def status(bundle):
    """The Review & Submit button's three original checks. Use met() for everything."""
    return {
        'Citation_Information': REQUIREMENTS[0].satisfied(bundle),
        'Modification_History': REQUIREMENTS[1].satisfied(bundle),
        'Targets': REQUIREMENTS[2].satisfied(bundle),
    }


def met(bundle):
    """True when nothing ELSA requires is outstanding."""
    return not requirements(bundle)
