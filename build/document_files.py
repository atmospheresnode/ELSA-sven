"""The file behind a document product: what is accepted, how it is named, where it lives.

Until this, ELSA could not attach a file to a document at all. The forms recorded a
typed file name and the label pointed at it, but no file ever existed, so validate
reported "URI reference does not exist" on every document in every bundle.

What is accepted is PDS policy, not preference. The "Policy on Formats for PDS4 Data
and Documentation" (and Standards Reference 8A.2.2) allows documentation as flat UTF-8
text, PDF/A-1a or PDF/A-1b, and validate itself only accepts PDF/A-1 ("Expected: 1a or
1b"). So:

* .pdf, and the file must declare PDF/A-1 in its own XMP metadata. That is a claim, not
  proof, but it is what separates an archival export from an ordinary one, and it is
  read in Python without running any other program. validate checks full conformance
  with veraPDF when it checks the bundle (for Archive documents).
* .txt, which must decode as UTF-8. It is recorded as "7-Bit ASCII Text" when every byte
  is ASCII, and "UTF-8 Text" otherwise, the two values PDS4 accepts for plain text.

Ordinary PDFs are refused with instructions rather than converted. Ghostscript can make
PDF/A-1b, but on this server its -dSAFER mode was measured still writing files, and
running it on files strangers upload, as apache, on a shared machine, is not a risk to
take inside an upload.

The uploaded file's own name becomes the document's file name, cleaned to PDS4's rules.
There is no longer a separate typed name that can disagree with the file.
"""
import os
import re
import tempfile
import unicodedata

from django import forms
from django.conf import settings

# PDS4 file_name: starts alphanumeric; letters, digits, dash, underscore, dot; ends in
# a dot and an extension. Taken from the pattern in the PDS4 schema.
PDS_FILE_NAME = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9]|[-]|[_]|[.])*[.][a-zA-Z0-9]+$')

ALLOWED_EXTENSIONS = ('.pdf', '.txt')

# How a PDF/A file states what it is: pdfaid:part='1' (or "1", or an element).
PDFA_PART = re.compile(rb"pdfaid:part\s*(?:=\s*[\"']|>)\s*(\d)")

HOW_TO_MAKE_PDFA = (
    'PDS archives documents as PDF/A-1, a version of PDF made for long-term '
    'preservation. Export it again as PDF/A-1b: in Microsoft Word on Windows, File > '
    'Export > Create PDF/XPS, tick "ISO 19005-1 compliant (PDF/A)"; in LibreOffice, '
    'File > Export as PDF, tick "Archive (PDF/A, ISO 19005)" and choose PDF/A-1b; in '
    'Adobe Acrobat, save as PDF/A-1b. Then upload that file. Plain text (.txt) is '
    'accepted too.')


def max_upload_bytes():
    return getattr(settings, 'DOCUMENT_MAX_UPLOAD_MB', 100) * 1024 * 1024


def clean_file_name(name):
    """The uploaded file's name, made into a legal PDS4 file name, or ValidationError.

    Spaces become underscores and accents are dropped, which is what people mean; any
    other character PDS4 does not allow is removed. The extension is lower-cased.
    """
    base = os.path.basename(name or '').strip()
    stem, extension = os.path.splitext(base)
    extension = extension.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise forms.ValidationError(
            'Documents can be a PDF (.pdf) or plain text (.txt). "{}" is neither. {}'.format(
                base or 'The file', HOW_TO_MAKE_PDFA))

    stem = unicodedata.normalize('NFKD', stem).encode('ascii', 'ignore').decode('ascii')
    stem = re.sub(r'\s+', '_', stem.strip())
    stem = re.sub(r'[^A-Za-z0-9._-]', '', stem)
    stem = stem.lstrip('._-')
    cleaned = stem + extension
    if not stem or not PDS_FILE_NAME.match(cleaned):
        raise forms.ValidationError(
            'PDS file names may use letters, digits, dots, dashes and underscores, and '
            'must start with a letter or digit. Rename "{}" and upload it again.'.format(base))
    return cleaned


def pdfa_part(head_and_body):
    """The PDF/A part a PDF declares (1, 2, 3...), or None if it declares none."""
    match = PDFA_PART.search(head_and_body)
    return int(match.group(1)) if match else None


def standard_for_text(data):
    """PDS4's name for this plain text, or ValidationError if it is not UTF-8."""
    try:
        data.decode('ascii')
        return '7-Bit ASCII Text'
    except UnicodeDecodeError:
        pass
    try:
        data.decode('utf-8')
        return 'UTF-8 Text'
    except UnicodeDecodeError:
        raise forms.ValidationError(
            'This text file is not UTF-8, which is the only text encoding PDS accepts. '
            'Save it as UTF-8 (most editors offer this under "Save as" or "Encoding") '
            'and upload it again.')


class PreparedDocumentFile(object):
    """An upload that passed every check, waiting to be stored with its document."""

    def __init__(self, uploaded, file_name, standard):
        self.uploaded = uploaded
        self.file_name = file_name
        self.standard = standard


def prepare(uploaded):
    """Check an uploaded document file. Returns PreparedDocumentFile or raises
    forms.ValidationError with a message the user can act on."""
    file_name = clean_file_name(uploaded.name)

    size = getattr(uploaded, 'size', 0) or 0
    if size == 0:
        raise forms.ValidationError('"{}" is empty.'.format(uploaded.name))
    if size > max_upload_bytes():
        raise forms.ValidationError(
            '"{}" is larger than the {} MB a document may be.'.format(
                uploaded.name, getattr(settings, 'DOCUMENT_MAX_UPLOAD_MB', 100)))

    data = b''.join(uploaded.chunks())
    uploaded.seek(0)

    if file_name.endswith('.pdf'):
        if not data.startswith(b'%PDF-'):
            raise forms.ValidationError(
                '"{}" is named .pdf but is not a PDF file.'.format(uploaded.name))
        part = pdfa_part(data)
        if part != 1:
            raise forms.ValidationError(
                '"{}" is {} PDF/A-1. {}'.format(
                    uploaded.name,
                    'PDF/A-{}, not'.format(part) if part else 'an ordinary PDF, not',
                    HOW_TO_MAKE_PDFA))
        standard = 'PDF/A'
    else:
        standard = standard_for_text(data)

    return PreparedDocumentFile(uploaded, file_name, standard)


def file_path(product_document):
    """Where this document's file lives, or '' if it has no file name recorded."""
    if not product_document.file_name:
        return ''
    return os.path.join(product_document.directory(), product_document.file_name)


def has_file(product_document):
    path = file_path(product_document)
    return bool(path) and os.path.isfile(path)


def size(product_document):
    """The size in bytes of this document's file, or None if it has none on disk."""
    path = file_path(product_document)
    return os.path.getsize(path) if path and os.path.isfile(path) else None


def text_excerpt(product_document, limit=1200):
    """The opening of a plain text document, for its thumbnail on the bundle page.

    '' for PDFs and for documents without a file. Cut at a byte limit, so a character
    split by the cut is dropped rather than shown broken.
    """
    path = servable_path(product_document)
    if not path or not path.lower().endswith('.txt'):
        return ''
    with open(path, 'rb') as handle:
        head = handle.read(limit)
    return head.decode('utf-8', errors='ignore')


def content_type(product_document):
    """What to tell the browser a document's file is, so it can show it in place.

    Only the two formats uploads accept. Text is declared UTF-8 because that is all
    uploads let in, and ASCII is a subset of it.
    """
    if (product_document.file_name or '').lower().endswith('.pdf'):
        return 'application/pdf'
    return 'text/plain; charset=utf-8'


def servable_path(product_document):
    """The document's file, if it exists and really is inside the document collection.

    Documents saved before uploads existed carry a typed file_name that was never
    cleaned, so it is not trusted to stay in the directory.
    """
    path = file_path(product_document)
    if not path:
        return ''
    directory = os.path.realpath(product_document.directory())
    real = os.path.realpath(path)
    if os.path.dirname(real) != directory or not os.path.isfile(real):
        return ''
    return real


def name_taken(bundle, file_name, excluding=None):
    """Whether another document in this bundle already uses this file name.

    Compared without case: two names differing only in case are one file on a
    case-insensitive disk, and validate warns about case mismatches.
    """
    from build.models import Product_Document
    others = Product_Document.objects.filter(bundle=bundle)
    if excluding is not None and excluding.pk:
        others = others.exclude(pk=excluding.pk)
    return any((other.file_name or '').lower() == file_name.lower() for other in others)


def store(product_document, prepared):
    """Write a prepared upload next to the document's label and record it.

    Written to a temporary name first and moved into place, so a failure part way
    never leaves a truncated file where the label says the document is. A replaced file
    under a different name is removed afterwards. Saves the document.
    """
    directory = product_document.directory()
    os.makedirs(directory, exist_ok=True)
    old_path = file_path(product_document)

    handle, temporary = tempfile.mkstemp(dir=directory, prefix='.upload-')
    try:
        with os.fdopen(handle, 'wb') as out:
            for chunk in prepared.uploaded.chunks():
                out.write(chunk)
        destination = os.path.join(directory, prepared.file_name)
        os.replace(temporary, destination)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise

    if old_path and os.path.abspath(old_path) != os.path.abspath(destination) \
            and os.path.isfile(old_path):
        os.remove(old_path)

    product_document.file_name = prepared.file_name
    product_document.document_std_id = prepared.standard
    product_document.encoding_standard_id = prepared.standard
    product_document.files = '1'
    product_document.save()
    return destination


def remove(product_document):
    """Delete this document's file, if it has one. Call before deleting the record."""
    path = file_path(product_document)
    if path and os.path.isfile(path):
        os.remove(path)
