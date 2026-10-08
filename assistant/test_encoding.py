"""The knowledge base loads under Apache's locale, not just under runserver's.

Production went down on 2026-09-25: every page returned 500. The retriever read the
knowledge chunks with Path.read_text() and no encoding, which means the locale's
encoding. Under runserver that is UTF-8; under Apache/mod_wsgi it is ASCII. A single
"..." written as one character in a chunk then raised UnicodeDecodeError, and because
the chunks are loaded at import time, from the URL configuration, ELSA could not start.
Every local test passed, because every local test ran under UTF-8.

So this runs the loader in a child Python with the locale forced to ASCII, with the two
features that would otherwise quietly switch it back to UTF-8 turned off (PEP 538 locale
coercion and PEP 540 UTF-8 mode), and hands it a chunk with a non-ASCII character in it.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

REPO = Path(__file__).resolve().parent.parent

SCRIPT = r"""
import locale, sys
from pathlib import Path
enc = locale.getpreferredencoding(False).lower()
if 'ascii' not in enc and enc not in ('ansi_x3.4-1968',):
    print('NOT-ASCII-LOCALE', enc)
    sys.exit(3)
sys.path.insert(0, sys.argv[1])
from assistant import retriever
retriever.KNOWLEDGE_DIR = Path(sys.argv[2])
chunks = retriever._load_chunks()
print('LOADED', len(chunks), chunks[0]['text'].strip())
"""


def ascii_environment():
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('LC_') and key not in ('LANG', 'LANGUAGE')}
    environment.update({'LC_ALL': 'C', 'LANG': 'C', 'PYTHONUTF8': '0',
                        'PYTHONCOERCECLOCALE': '0', 'PYTHONIOENCODING': 'utf-8'})
    return environment


class KnowledgeLoadsUnderAnAsciiLocaleTests(SimpleTestCase):

    def run_loader(self, text):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'chunk.md').write_text(text, encoding='utf-8')
            return subprocess.run(
                [sys.executable, '-c', SCRIPT, str(REPO), directory],
                capture_output=True, text=True, encoding='utf-8', env=ascii_environment(),
                timeout=60)

    def test_a_non_ascii_character_does_not_stop_the_loader(self):
        result = self.run_loader('Checking… then 5° and Juberías\n')
        self.assertNotEqual(result.returncode, 3, 'the child did not get an ASCII locale, '
                                                  'so this test proves nothing: ' + result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertIn('Checking…', result.stdout)

    def test_every_shipped_chunk_loads_under_an_ascii_locale(self):
        """The real knowledge directory, the way production imports it."""
        script = ('import sys; sys.path.insert(0, sys.argv[1]); '
                  'from assistant import retriever; print(len(retriever._CHUNKS))')
        result = subprocess.run([sys.executable, '-c', script, str(REPO)],
                                capture_output=True, text=True, encoding='utf-8',
                                env=ascii_environment(), timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertGreater(int(result.stdout.strip()), 0)
