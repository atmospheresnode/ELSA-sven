# -*- coding: utf-8 -*-
"""What a validation result depends on besides the bundle's files.

A result goes out of date when the files change, or when anything here changes: the
validate release, the schema catalog, the code that reads the report and decides what
blocks, the repairs run before each check, and the label upgrades.
"""
import ast
import hashlib
import inspect
import os

from django.conf import settings

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))

# Code that decides what a check finds or how it is judged.
CHECK_MODULES = ('validate_report.py', 'validate_rules.py', 'label_repair.py')

_cache = {}


def _code_digest(source):
    """Digest of Python source that ignores comments and formatting."""
    try:
        return hashlib.sha256(ast.dump(ast.parse(source)).encode('utf-8')).hexdigest()
    except SyntaxError:
        return hashlib.sha256(source.encode('utf-8')).hexdigest()


def _file_digest(path):
    if not os.path.exists(path):
        return ''
    with open(path, encoding='utf-8') as handle:
        return _code_digest(handle.read())


def _catalog_path():
    from build.validate_runner import work_dir
    return os.path.join(work_dir(), 'catalog.xml')


def _validate_release():
    """The release VALIDATE_HOME points at, following the managed link."""
    home = getattr(settings, 'VALIDATE_HOME', '')
    return os.path.realpath(home) if home else ''


def _stamp(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def checker_version():
    """A short digest of everything above. Same inputs, same answer."""
    from build import label_migrations

    modules = [os.path.join(BUILD_DIR, name) for name in CHECK_MODULES]
    runner_path = os.path.join(BUILD_DIR, 'validate_runner.py')
    catalog = _catalog_path()
    key = (tuple(_stamp(path) for path in modules + [runner_path, catalog]),
           _validate_release(),
           bool(getattr(settings, 'VALIDATE_SKIP_CONTENT', False)),
           label_migrations.head())
    if key in _cache:
        return _cache[key]

    from build import validate_runner

    digest = hashlib.sha256()
    for path in modules:
        digest.update(_file_digest(path).encode('utf-8'))
    # Only the command line, not the rest of the runner.
    digest.update(_code_digest(inspect.getsource(validate_runner.build_command)).encode('utf-8'))
    if os.path.exists(catalog):
        with open(catalog, 'rb') as handle:
            digest.update(hashlib.sha256(handle.read()).hexdigest().encode('utf-8'))
    for part in key[1:]:
        digest.update(repr(part).encode('utf-8'))

    version = digest.hexdigest()[:16]
    _cache.clear()
    _cache[key] = version
    return version
