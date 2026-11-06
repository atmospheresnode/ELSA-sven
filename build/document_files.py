"""The file behind a document product: what's accepted, how it's named, where it goes.

PDS allows flat UTF-8 text, PDF/A-1a or PDF/A-1b (Standards Reference 8A.2.2):

* .pdf has to declare PDF/A-1 in its XMP metadata. validate checks full conformance with veraPDF.
* .txt has to decode as UTF-8. Recorded as "7-Bit ASCII Text" if all ASCII, otherwise "UTF-8 Text".

Ordinary PDFs are refused with instructions. We don't convert them with Ghostscript since
its -dSAFER mode still wrote files on this server.

The uploaded file's name, cleaned up, becomes the document's file name.
"""
import os
import re
import tempfile
import unicodedata

from django import forms
from django.conf import settings

# PDS4 file_name pattern: starts alphanumeric, letters, digits, dash, underscore, dot, ends in an extension.
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
    """The upload's name as a legal PDS4 file name, or ValidationError.

    Spaces become underscores, accents are dropped, and the extension is lower-cased.
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
    """Check an uploaded document file. Returns PreparedDocumentFile or raises ValidationError."""
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
    """Start of a plain text document for its thumbnail. '' for PDFs and no file."""
    path = servable_path(product_document)
    if not path or not path.lower().endswith('.txt'):
        return ''
    with open(path, 'rb') as handle:
        head = handle.read(limit)
    return head.decode('utf-8', errors='ignore')


def content_type(product_document):
    """Content type so the browser can show a document's file in place."""
    if (product_document.file_name or '').lower().endswith('.pdf'):
        return 'application/pdf'
    return 'text/plain; charset=utf-8'


def servable_path(product_document):
    """The document's file, if it exists and is inside the document collection.

    Older documents have a typed file_name that was never cleaned, so don't trust it.
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
    """Whether another document in this bundle uses this file name, ignoring case."""
    from build.models import Product_Document
    others = Product_Document.objects.filter(bundle=bundle)
    if excluding is not None and excluding.pk:
        others = others.exclude(pk=excluding.pk)
    return any((other.file_name or '').lower() == file_name.lower() for other in others)


def store(product_document, prepared):
    """Write an upload next to the document's label and save the document.

    Written to a temp name and moved into place. A replaced file with a different name is removed.
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
