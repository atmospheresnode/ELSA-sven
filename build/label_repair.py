# -*- coding: utf-8 -*-
"""Repair labels written before the generator was fixed.

A bundle built today validates clean: the Archive bundle I built to check produced
sixteen findings, every one of them the user's own (empty collections, no citation),
and nothing attributable to ELSA. A bundle built months ago does not, because its
labels are files on disk and no fix reaches back into them. One real Archive bundle
carries 119 findings, 115 of which ELSA put there.

So this repairs rather than regenerates. Regenerating from the templates would be
simpler and would lose data: author and editor names live only in the XML, never in
the database, so rebuilding a label from a template and the model would silently
discard every name a user ever typed. Repair touches only what is demonstrably
wrong and leaves everything else exactly as it is.

Two defects, both of which PDS reports many times over:

* Empty stubs the template carried and ELSA never filled. An element with no value
  is not valid PDS4, so an optional container nobody filled has to be absent rather
  than blank. The set below is exactly the containers PDS4 marks minOccurs="0",
  checked against the schema rather than remembered.
* Inventories that were never written, leaving a collection declaring zero members
  and naming an inventory file that is not there.
"""
from __future__ import unicode_literals

import os

from lxml import etree

from build.chocolate import close_label, open_label_with_tree

PDS_NS = '{http://pds.nasa.gov/pds4/pds/v1}'

# What PDS4 marks minOccurs="0", by the parent it appears in. Derived from the
# schema rather than remembered, and keyed by parent on purpose: "description" is
# optional inside Document and required inside External_Reference, so a flat list of
# names would eventually prune something that had to stay.
#
# An element not listed for its parent is left alone even when empty. Dropping a
# required element trades one error for another and loses information while doing it.
OPTIONAL_BY_PARENT = {
    'Identification_Area': ('Alias_List', 'License_Information',
                            'Modification_History'),
    'Context_Area': ('comment', 'Time_Coordinates', 'Primary_Result_Summary',
                     'Investigation_Area', 'Observing_System',
                     'Target_Identification', 'Mission_Area'),
    'Reference_List': ('Source_Product_Internal', 'Source_Product_External',
                       'Internal_Reference', 'External_Reference'),
    'File': ('comment', 'creation_date_time', 'file_URL', 'file_size',
             'local_identifier', 'md5_checksum', 'records'),
    'Document': ('acknowledgement_text', 'author_list', 'copyright', 'description',
                 'document_editions', 'doi', 'editor_list', 'revision_id'),
    'Document_Edition': ('description', 'starting_point_identifier'),
    'Internal_Reference': ('comment',),
    'External_Reference': ('description', 'doi'),
    'Document_File': ('directory_path_name',),
    'Observing_System': ('description', 'name'),
    'Time_Coordinates': ('local_mean_solar_time', 'local_true_solar_time',
                         'solar_longitude'),
}

# Flat view, for the tests that assert nothing protected is also prunable.
OPTIONAL_CONTAINERS = tuple(sorted(
    {name for names in OPTIONAL_BY_PARENT.values() for name in names}))

# Never pruned, whatever it looks like. Citation_Information holds the author and
# editor names, which exist nowhere else; an Observation_Area requires the same
# children Context_Area makes optional; and the AMA content is a dictionary of its
# own that this has no business reasoning about.
PROTECTED = ('Citation_Information', 'Observation_Area', 'Discipline_Area')


def _is_blank(element):
    """True when this element carries no value anywhere beneath it."""
    if (element.text or '').strip():
        return False
    for descendant in element.iter():
        if descendant is element:
            continue
        if (descendant.text or '').strip():
            return False
        if descendant.tail and descendant.tail.strip():
            return False
    return True


def prune_blank_containers(root):
    """Remove optional containers that carry no value. Returns what was removed.

    Bottom-up, so a container whose only children were themselves blank is seen as
    blank once they are gone: an Observing_System holding one empty
    Observing_System_Component is blank, and removing the component first is what
    makes that visible.
    """
    removed = []
    protected = set(PROTECTED)

    for element in reversed(list(root.iter())):
        name = etree.QName(element).localname
        if name in protected:
            continue
        parent = element.getparent()
        if parent is None:
            continue

        # Anywhere inside a protected subtree, not merely its immediate children:
        # a blank given_name sits two levels below Citation_Information.
        if any(etree.QName(a).localname in protected
               for a in element.iterancestors()):
            continue

        if name not in OPTIONAL_BY_PARENT.get(
                etree.QName(parent).localname, ()):
            continue

        if _is_blank(element):
            parent.remove(element)
            removed.append(name)

    return removed


def repair_label(label_path):
    """Repair one label in place. Returns the list of containers removed."""
    if not os.path.exists(label_path):
        return []

    try:
        _path, root, tree = open_label_with_tree(label_path)
    except (etree.XMLSyntaxError, OSError):
        return []

    removed = prune_blank_containers(root)
    if removed:
        close_label(label_path, root, tree)
    return removed


# -- the bundle's member list ------------------------------------------------------
#
# A third defect, in the bundle label only. Deleting a collection never removed its
# Bundle_Member_Entry (AdditionalCollections had no remove_xml, and the failure was
# caught and printed), and creating one never checked for an existing entry. So a
# bundle whose user deleted collections lists members that do not exist, which PDS
# reports as "member ... could not be found", and one whose user recreated a
# collection of the same name lists it several times, which PDS reports as "Inventory
# contains 3 instances of LIDVID".

def _collection_lids_on_disk(directory):
    """The LID of every Product_Collection label under a bundle directory."""
    lids = set()
    for dirpath, _dirnames, filenames in os.walk(directory):
        for filename in filenames:
            if not filename.lower().endswith('.xml'):
                continue
            try:
                root = etree.parse(os.path.join(dirpath, filename)).getroot()
            except (etree.XMLSyntaxError, OSError):
                continue
            if etree.QName(root).localname != 'Product_Collection':
                continue
            lid = root.find('{0}Identification_Area/{0}logical_identifier'.format(PDS_NS))
            if lid is not None and (lid.text or '').strip():
                lids.add(lid.text.strip())
    return lids


def stale_bundle_members(bundle):
    """Entries in the bundle label to remove, as [(lid, reason)], in label order.

    Deliberately narrow. An entry goes only when it repeats one already listed, or when
    nothing by that name exists at all: no collection directory, no collection label
    and no collection in the database. A collection that exists in any of those places
    keeps its entry, even if something else about it is wrong, because dropping a real
    member from a bundle is worse than the error it would fix.

    Compared by the LID's last segment, ignoring case, not by the whole LID. Bundles
    from 2022 on production carry collection labels whose LID ends ":Document" under a
    bundle entry ending ":document"; matched exactly, 159 live document and context
    collections read as orphans and would have been removed. The directory is what a
    deletion reliably takes away: delete_collection removes it along with the label.
    """
    from build.models import (AdditionalCollections, Product_Bundle, Product_Collection,
                              bundle_member_entries)

    try:
        label_path = Product_Bundle.objects.get(bundle=bundle).label()
    except Product_Bundle.DoesNotExist:
        return []
    if not label_path or not os.path.exists(label_path):
        return []
    try:
        _path, root, _tree = open_label_with_tree(label_path)
    except (etree.XMLSyntaxError, OSError):
        return []

    def segment(lid):
        return lid.rsplit(':', 1)[-1].strip().lower()

    directory = bundle.directory()
    alive = {segment(lid) for lid in _collection_lids_on_disk(directory)}
    alive.update(name.lower() for name in os.listdir(directory)
                 if os.path.isdir(os.path.join(directory, name)))
    alive.update(collection.collection_name.lower()
                 for collection in AdditionalCollections.objects.filter(bundle=bundle))
    # The collections a bundle is built with. An Archive bundle's "data" collection is
    # listed in the bundle label but deliberately never given a label or a directory
    # (see the build view), so only its row says it exists: 77 production bundles.
    alive.update(str(collection.collection).lower()
                 for collection in Product_Collection.objects.filter(bundle=bundle))

    stale, seen = [], set()
    for _element, lid in bundle_member_entries(root):
        if lid in seen:
            stale.append((lid, 'listed more than once'))
        elif segment(lid) not in alive:
            stale.append((lid, 'no such collection'))
        seen.add(lid)
    return stale


def repair_bundle_members(bundle):
    """Remove duplicate and orphaned member entries from the bundle label, in place.

    Returns what stale_bundle_members reported, which is what was removed. Keeps the
    first entry of any LID listed more than once.
    """
    from build.models import Product_Bundle, bundle_member_entries

    stale = stale_bundle_members(bundle)
    if not stale:
        return []

    label_path = Product_Bundle.objects.get(bundle=bundle).label()
    _path, root, tree = open_label_with_tree(label_path)
    orphaned = {lid for lid, reason in stale if reason == 'no such collection'}
    seen = set()
    for element, lid in bundle_member_entries(root):
        if lid in orphaned or lid in seen:
            root.remove(element)
        seen.add(lid)
    close_label(label_path, root, tree)
    return stale
