# -*- coding: utf-8 -*-
"""Numbered fixes for labels already on disk, applied once to each bundle.

Like database migrations, but for bundle files. Each module is named NNNN_name.py and has:

    title         what changed, in words a user understands
    apply(bundle) makes the fix and returns a list of short change notes

A bundle remembers the last number it has (Bundle.label_revision). New bundles start at
the newest, since their labels come from fixed code. build.label_upgrades applies the rest.

Rules for a migration:
    - safe to run twice on the same bundle
    - only writes labels and inventory tables, never data files
    - raises if it can't finish, so the bundle's files get put back

When you change what a label looks like, add a migration so old bundles get it too.
"""
import importlib
import os
import re

DIRECTORY = os.path.dirname(os.path.abspath(__file__))
NAME = re.compile(r'^(\d{4})_(\w+)\.py$')


class Migration(object):
    def __init__(self, number, name, module):
        self.number = number
        self.name = name
        self.module = module
        self.title = getattr(module, 'title', name.replace('_', ' '))
        self.bundle_types = getattr(module, 'bundle_types', None)

    def applies_to(self, bundle):
        return self.bundle_types is None or bundle.bundle_type in self.bundle_types

    def apply(self, bundle):
        return list(self.module.apply(bundle) or [])

    def __repr__(self):
        return '<label migration {:04d}_{}>'.format(self.number, self.name)


def _files():
    found = []
    for filename in os.listdir(DIRECTORY):
        match = NAME.match(filename)
        if match:
            found.append((int(match.group(1)), match.group(2)))
    return sorted(found)


def head():
    """The newest migration number, 0 if there are none. Reads file names only."""
    files = _files()
    return files[-1][0] if files else 0


def all_migrations():
    migrations = []
    for number, name in _files():
        module = importlib.import_module('{}.{:04d}_{}'.format(__name__, number, name))
        migrations.append(Migration(number, name, module))
    numbers = [migration.number for migration in migrations]
    if len(numbers) != len(set(numbers)):
        raise RuntimeError('Two label migrations share a number: {}'.format(numbers))
    return migrations


def pending(bundle):
    """Migrations this bundle hasn't had yet, in order."""
    revision = bundle.label_revision or 0
    return sorted((migration for migration in all_migrations() if migration.number > revision),
                  key=lambda migration: migration.number)
