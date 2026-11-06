"""Turns a validate JSON report into findings ELSA can use.

validate gives a line number. We resolve that back to the element's path in the label, which
says which card fixes it. These are pure functions over a report and its labels, so
they can run without a database.
"""
import json
import os
from urllib.parse import unquote

from lxml import etree

PDS_NS = '{http://pds.nasa.gov/pds4/pds/v1}'

# Same shape in both. Product results are per label, bundle results are the reference checks.
RESULT_KEYS = ('productLevelValidationResults', 'pDS4BundleLevelValidationResults')


def element_path(element):
    """Slash-joined tag names from the root down to this element, namespaces stripped."""
    parts = []
    current = element
    while current is not None and isinstance(current.tag, str):
        parts.append(current.tag.replace(PDS_NS, ''))
        current = current.getparent()
    return '/'.join(reversed(parts))


def _line_index(label_path):
    """Map each line number in a label to the element that starts on it.

    Use setdefault so the outermost element on a line wins.
    """
    # Some findings are about a directory. libxml2 would print an I/O error for those.
    if not os.path.isfile(label_path):
        return {}
    try:
        tree = etree.parse(label_path)
    except (OSError, etree.XMLSyntaxError):
        # The label can be unreadable if it's broken. Findings still come back, just with no path.
        return {}

    index = {}
    for element in tree.iter():
        if isinstance(element.tag, str):
            index.setdefault(element.sourceline, element)
    return index


def parse_report(report_path):
    """Read a validate JSON report into a flat list of findings plus a summary.

    Returns {'summary': {...}, 'findings': [...]}.
    """
    with open(report_path, encoding='utf-8') as report_file:
        report = json.load(report_file)

    summary = report.get('summary') or {}
    findings = []
    seen = set()
    line_indexes = {}

    for key in RESULT_KEYS:
        for result in report.get(key) or []:
            # validate gives a file: URI, so decode it or a space stays as %20.
            label_path = unquote((result.get('label') or '').replace('file:', ''))
            label_name = os.path.basename(label_path)

            if label_path and label_path not in line_indexes:
                line_indexes[label_path] = _line_index(label_path)
            index = line_indexes.get(label_path, {})

            for message in result.get('messages') or []:
                line = message.get('line')
                element = index.get(line)
                path = element_path(element) if element is not None else ''

                finding = {
                    'severity': (message.get('severity') or '').upper(),
                    'type': message.get('type') or '',
                    'message': ' '.join(str(message.get('message') or '').split()),
                    'label': label_name,
                    'label_path': label_path,
                    'line': line,
                    'element_path': path,
                }

                # Drop identical findings. Keyed on the full path since two collections can have the same file name.
                fingerprint = (finding['label_path'], finding['type'], finding['line'],
                               finding['message'])
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                findings.append(finding)

    # Release from the report's header. Blank if it isn't there.
    configuration = report.get('configuration')
    if not isinstance(configuration, dict):
        configuration = {}
    tool_version = str(configuration.get('version') or '').strip()

    return {
        'summary': {
            'tool_version': tool_version,
            'errors': summary.get('totalErrors', 0),
            'warnings': summary.get('totalWarnings', 0),
            'products': summary.get('totalProducts', 0),
            'message_types': {entry.get('messageType'): entry.get('total')
                              for entry in summary.get('messageTypes') or []},
        },
        'findings': findings,
    }


def errors(findings):
    # validate reports schematron failures as FATAL_ERROR, so count those too.
    return [finding for finding in findings if finding['severity'] in ('ERROR', 'FATAL_ERROR')]


def warnings(findings):
    return [finding for finding in findings if finding['severity'] == 'WARNING']


def group_by_location(findings):
    """{element_path: [findings]}. Findings with no path go under ''."""
    grouped = {}
    for finding in findings:
        grouped.setdefault(finding['element_path'], []).append(finding)
    return grouped


def summarise_types(findings):
    """{message type: count}, for the staff view and for building the rule table."""
    counts = {}
    for finding in findings:
        counts[finding['type']] = counts.get(finding['type'], 0) + 1
    return counts
