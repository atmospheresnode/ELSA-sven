# -*- coding: utf-8 -*-
"""Repair labels that were written before the generator fixes.

Labels are files on disk, so a code fix doesn't reach old ones. We repair instead of
regenerating because author and editor names are only in the XML, not the database.
Rebuilding from a template would lose them.

What gets fixed:

* Empty optional containers from the template. PDS4 rejects empty elements, so they
  have to be removed. The list below comes from what the schema marks minOccurs="0".
* Inventories that never got written.
* AMA labels: investigation, nil times on collections, collection titles, "nan" values
  and the placeholder comment.
"""
from __future__ import unicode_literals

import os
import re

from lxml import etree

from build.chocolate import close_label, open_label_with_tree

PDS_NS = '{http://pds.nasa.gov/pds4/pds/v1}'
AMA_NS = '{http://pds.nasa.gov/pds4/ama/v1}'
XSI_NIL = '{http://www.w3.org/2001/XMLSchema-instance}nil'

# What PDS4 marks minOccurs="0", by parent. By parent since "description" is optional in
# Document and required in External_Reference. Anything not listed is left alone even if empty.
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
    'Product_Collection': ('Reference_List',),
    'Product_External': ('Reference_List',),
}

# Flat view, for the tests that assert nothing protected is also prunable.
OPTIONAL_CONTAINERS = tuple(sorted(
    {name for names in OPTIONAL_BY_PARENT.values() for name in names}))

# Never pruned. Citation_Information has the names, Observation_Area needs its children,
# and AMA content has its own dictionary.
PROTECTED = ('Citation_Information', 'Observation_Area', 'Discipline_Area')


def _is_blank(element):
    """True if this element has no value anywhere under it. xsi:nil counts as a value."""
    if (element.text or '').strip():
        return False
    for descendant in element.iter():
        if descendant.get(XSI_NIL) == 'true':
            return False
        if descendant is element:
            continue
        if (descendant.text or '').strip():
            return False
        if descendant.tail and descendant.tail.strip():
            return False
    return True


def prune_blank_containers(root):
    """Remove optional containers with no value. Returns what was removed. Works bottom-up."""
    removed = []
    protected = set(PROTECTED)

    for element in reversed(list(root.iter())):
        # Comments and processing instructions have no tag name, and QName raises on them.
        if not isinstance(element.tag, str):
            continue
        name = etree.QName(element).localname
        if name in protected:
            continue
        parent = element.getparent()
        if parent is None:
            continue

        # Anywhere under a protected element, not just its direct children.
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


# -- the AMA investigation reference --
# 
# Migration 0075 fixed the LID in the database but not in labels on disk. Same values as
# the migration, not imported since the module name starts with a digit.
AMA_WRONG_LID = ('urn:nasa:pds:context:investigation:'
                 'individual_investigation.atmospheric-modeling-annex')
AMA_LID = 'urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex'
# The title PDS publishes for it, which the current templates write.
AMA_NAME = 'Atmospheric Modeling Annex Individual Investigation'


def _local(element):
    return etree.QName(element).localname if isinstance(element.tag, str) else ''


def fix_ama_investigation(root):
    """Point an Investigation_Area with the old AMA LID at the right one, and fix its name.

    Returns one entry per area fixed.
    """
    fixed = []
    for area in root.iter():
        if _local(area) != 'Investigation_Area':
            continue
        references = [element for element in area.iter() if _local(element) == 'lid_reference'
                      and (element.text or '').strip() == AMA_WRONG_LID]
        if not references:
            continue
        for reference in references:
            reference.text = AMA_LID
        for child in area:
            if _local(child) == 'name':
                child.text = AMA_NAME
        fixed.append('AMA investigation reference')
    return fixed


def fix_organizations(root):
    """Remove blank ROR IDs and parent organizations from citation Organizations.

    Separate from the pruning because Citation_Information is protected there.
    """
    fixed = []
    for tag in ('List_Author', 'List_Editor'):
        for people in root.iter(PDS_NS + tag):
            organizations = people.findall(PDS_NS + 'Organization')
            for organization in organizations:
                rorid = organization.find(PDS_NS + 'organization_rorid')
                if rorid is not None and not (rorid.text or '').strip():
                    organization.remove(rorid)
                    fixed.append('blank ROR ID')
                parent = organization.find(PDS_NS + 'Parent_Organization')
                if parent is not None and _is_blank(parent):
                    organization.remove(parent)
                    fixed.append('blank parent organization')
    return fixed


def _is_ama_label(root):
    lid = root.find('{0}Identification_Area/{0}logical_identifier'.format(PDS_NS))
    return lid is not None and (lid.text or '').strip().startswith('urn:nasa:pds-ama:')


def ensure_ama_investigation(root):
    """Add the AMA investigation to a bundle or collection label that doesn't have it."""
    from build.models import insert_in_context_area

    kind = {'Product_Bundle': 'bundle', 'Product_Collection': 'collection'}.get(_local(root))
    context = root.find(PDS_NS + 'Context_Area')
    if kind is None or context is None or context.find(PDS_NS + 'Investigation_Area') is not None:
        return []

    area = etree.Element(PDS_NS + 'Investigation_Area')
    etree.SubElement(area, PDS_NS + 'name').text = AMA_NAME
    etree.SubElement(area, PDS_NS + 'type').text = 'Individual Investigation'
    reference = etree.SubElement(area, PDS_NS + 'Internal_Reference')
    etree.SubElement(reference, PDS_NS + 'lid_reference').text = AMA_LID
    etree.SubElement(reference, PDS_NS + 'reference_type').text = '{}_to_investigation'.format(kind)
    insert_in_context_area(context, area)
    return ['AMA investigation added']


def ensure_nil_time_coordinates(root):
    """Add nil start and stop times to an AMA collection label that doesn't have them.

    Model output has no observation time. Run this after the pruning.
    """
    from build.models import insert_in_context_area

    context = root.find(PDS_NS + 'Context_Area')
    if (_local(root) != 'Product_Collection' or context is None
            or context.find(PDS_NS + 'Time_Coordinates') is not None):
        return []

    times = etree.Element(PDS_NS + 'Time_Coordinates')
    for tag in ('start_date_time', 'stop_date_time'):
        element = etree.SubElement(times, PDS_NS + tag)
        element.set(XSI_NIL, 'true')
        element.set('nilReason', 'inapplicable')
    insert_in_context_area(context, times)
    return ['nil time coordinates added']


def remove_ama_nan_values(root):
    """Remove "nan" values from AMA Variable and Coordinate classes.

    These fields are all optional. Names are skipped since a variable could be called nan.
    """
    fixed = []
    for container in ('Variable', 'Coordinate'):
        for element in root.iter(AMA_NS + container):
            for child in list(element):
                if _local(child) in ('variable_name', 'coord_name'):
                    continue
                if (child.text or '').strip().lower() == 'nan':
                    element.remove(child)
                    fixed.append('nan value')
    return fixed


PLACEHOLDER_COMMENT = re.compile(r'^netCDF file for output\s*-*\s*$')


def remove_placeholder_comment(root):
    """Remove the template's placeholder comment from File_Area_External/File."""
    fixed = []
    for area in root.iter(PDS_NS + 'File_Area_External'):
        for comment in area.iter(PDS_NS + 'comment'):
            if PLACEHOLDER_COMMENT.match((comment.text or '').strip()):
                comment.getparent().remove(comment)
                fixed.append('placeholder comment')
    return fixed


def fix_ama_title(root):
    """Fix the casing of an AMA title and add the collection name to collection titles."""
    from build.models import collection_title, pds_title_case

    kind = _local(root)
    if kind not in ('Product_Bundle', 'Product_Collection'):
        return []
    identification = root.find(PDS_NS + 'Identification_Area')
    title = None if identification is None else identification.find(PDS_NS + 'title')
    lid = None if identification is None else identification.find(PDS_NS + 'logical_identifier')
    if title is None or lid is None or not (title.text or '').strip():
        return []

    wanted = pds_title_case(title.text.strip())
    if kind == 'Product_Collection' and not wanted.endswith(' Collection'):
        wanted = collection_title(wanted, (lid.text or '').rsplit(':', 1)[-1])
    if wanted == title.text:
        return []
    title.text = wanted
    return ['title']


def repair_root(root):
    """Run all the label repairs on a parsed label. Returns what changed."""
    fixes = prune_blank_containers(root) + fix_ama_investigation(root)
    fixes += fix_organizations(root)
    if _is_ama_label(root):
        fixes += ensure_ama_investigation(root) + ensure_nil_time_coordinates(root)
        fixes += remove_ama_nan_values(root) + remove_placeholder_comment(root)
        fixes += fix_ama_title(root)
    return fixes


def repair_label(label_path):
    """Repair one label in place. Returns what was fixed."""
    if not os.path.exists(label_path):
        return []

    try:
        _path, root, tree = open_label_with_tree(label_path)
    except (etree.XMLSyntaxError, OSError):
        return []

    fixes = repair_root(root)
    if fixes:
        close_label(label_path, root, tree)
    return fixes


def inventory_needs_writing(label_path):
    """True if a collection's inventory file is missing or doesn't match the label."""
    try:
        root = etree.parse(label_path).getroot()
    except (etree.XMLSyntaxError, OSError):
        return False
    area = root.find(PDS_NS + 'File_Area_Inventory')
    if area is None:
        return False
    file_name = (area.findtext('{0}File/{0}file_name'.format(PDS_NS)) or '').strip()
    if not file_name:
        return True
    table = os.path.join(os.path.dirname(label_path), file_name)
    if not os.path.exists(table):
        return True
    with open(table, 'rb') as handle:
        rows = handle.read().count(b'\r\n')
    return (area.findtext('{0}Inventory/{0}records'.format(PDS_NS)) or '').strip() != str(rows)


def repair_bundle(bundle):
    """Repair one bundle's labels. Returns the fixes made.

    Only write when something changed. A write updates the file's modified time, and that
    marks the validation result out of date.
    """
    directory = bundle.directory()
    if not os.path.isdir(directory):
        return []

    fixes = []
    for dirpath, _dirnames, filenames in os.walk(directory):
        for filename in sorted(filenames):
            if filename.lower().endswith('.xml'):
                fixes.extend(repair_label(os.path.join(dirpath, filename)))

    fixes.extend('member entry: {}'.format(reason) for _lid, reason in repair_bundle_members(bundle))

    from build.models import (AdditionalCollections, Product_Collection,
                              mirror_citation_into_data_products)
    # Do this after the labels so the repaired citation gets copied.
    mirrored = mirror_citation_into_data_products(bundle)
    if mirrored:
        fixes.append('citation copied into {} product label(s)'.format(mirrored))
    collections = list(Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'))
    collections.extend(AdditionalCollections.objects.filter(bundle=bundle))
    for collection in collections:
        try:
            if inventory_needs_writing(collection.label()):
                collection.build_inventory()
                fixes.append('inventory')
        except Exception as error:                       # noqa: BLE001 - one collection
            # Keep going if one collection fails. Validate will still report the problem.
            print('repair_bundle: inventory of {} failed: {}'.format(collection, error))
    return fixes


def stray_root_files(bundle):
    """Files in the bundle's top folder that no label describes.

    Only reported, never removed, since they could be a user's data.
    """
    from build.models import Product_Bundle

    directory = bundle.directory()
    if not os.path.isdir(directory):
        return []
    try:
        bundle_label = os.path.basename(Product_Bundle.objects.get(bundle=bundle).label())
    except (Product_Bundle.DoesNotExist, AttributeError, TypeError):
        bundle_label = None
    return sorted(name for name in os.listdir(directory)
                  if os.path.isfile(os.path.join(directory, name)) and name != bundle_label)


# -- the bundle's member list --
# 
# Fixes duplicate entries and entries for collections that were deleted.

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
    """Entries to remove from the bundle label, as [(lid, reason)], in label order.

    Only removes a repeat, or an entry with no collection directory, label or database row.
    Compares the LID's last segment ignoring case, since older labels end in ":Document".
    """
    from build.models import (AdditionalCollections, Product_Bundle, Product_Collection,
                              bundle_member_entries)

    try:
        label_path = Product_Bundle.objects.get(bundle=bundle).label()
    except (Product_Bundle.DoesNotExist, AttributeError, TypeError):
        # Some prod bundles have no bundleID, so no path can be built. Nothing to compare.
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
    # Archive's "data" collection has no label or directory on purpose, only a row.
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
    """Remove duplicate and orphaned entries from the bundle label. Keeps the first of any repeat.

    Returns what stale_bundle_members reported.
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
