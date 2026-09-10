"""Turn a validate JSON report into findings ELSA can act on.

validate reports a line number. ELSA needs to know which card fixes the problem,
and the bridge between the two is the label itself: because ELSA generated every
element, resolving a line back to its XML path says which part of the bundle a
finding is about. `Identification_Area/Modification_History` is a thing ELSA has a
form for; "line 10" is not.

Everything here is a pure function over a report file and the labels it names, so it
can be tested without a database and re-run against reports gathered earlier - which
matters, because the rule table in the next phase should be built from findings
observed in production rather than guessed at.

Two message types carry most of the weight. error.label.schema and
error.label.schematron are catch-alls whose meaning lives in free text, so grouping
by type alone says very little; grouping by resolved path is what separates "your
citation is incomplete" from "this collection is empty".
"""
import json
import os

from lxml import etree

PDS_NS = '{http://pds.nasa.gov/pds4/pds/v1}'

# Both keys hold the same shape. Product-level results are per label; bundle-level
# results are the referential integrity checks that only exist for a -R pds4.bundle
# run. Missing either would silently drop findings.
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

    setdefault, not assignment: several elements can share a source line when a
    label is written compactly, and the first one is the outermost, which is the
    one a line-numbered finding is talking about.
    """
    try:
        tree = etree.parse(label_path)
    except (OSError, etree.XMLSyntaxError):
        # A label can be unreadable precisely because it is broken, which is often
        # what the report is complaining about. Findings still come back, just
        # without a resolved path.
        return {}

    index = {}
    for element in tree.iter():
        if isinstance(element.tag, str):
            index.setdefault(element.sourceline, element)
    return index


def parse_report(report_path):
    """Read a validate JSON report into a flat list of findings plus a summary.

    Returns {'summary': {...}, 'findings': [...]}. Each finding carries the resolved
    element_path when the label could be read and the message named a line.
    """
    with open(report_path, encoding='utf-8') as report_file:
        report = json.load(report_file)

    summary = report.get('summary') or {}
    findings = []
    seen = set()
    line_indexes = {}

    for key in RESULT_KEYS:
        for result in report.get(key) or []:
            label_path = (result.get('label') or '').replace('file:', '')
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

                # The same problem is reported once per result key when a label is
                # both a product and a bundle member, and the schema and schematron
                # can each flag one bad value. Identical findings are noise.
                fingerprint = (finding['label'], finding['type'], finding['line'],
                               finding['message'])
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                findings.append(finding)

    return {
        'summary': {
            'errors': summary.get('totalErrors', 0),
            'warnings': summary.get('totalWarnings', 0),
            'products': summary.get('totalProducts', 0),
            'message_types': {entry.get('messageType'): entry.get('total')
                              for entry in summary.get('messageTypes') or []},
        },
        'findings': findings,
    }


def errors(findings):
    return [finding for finding in findings if finding['severity'] == 'ERROR']


def warnings(findings):
    return [finding for finding in findings if finding['severity'] == 'WARNING']


def group_by_location(findings):
    """{element_path: [findings]}, which is the shape the eventual UI groups on.

    Findings with no resolved path collect under '' rather than being dropped: a
    finding nobody can place is still a finding, and losing it quietly would be
    worse than showing it without a location.
    """
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
