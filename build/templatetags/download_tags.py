# -*- coding: utf-8 -*-
"""{% download_choices bundle as options %}: the ways to download a bundle, for a menu."""
from django import template

from build import downloads

register = template.Library()


@register.simple_tag
def download_choices(bundle):
    folder = downloads.bundle_folder(bundle)
    try:
        return downloads.choices(folder) if folder else []
    except OSError:
        # The page still opens; the download itself says what is wrong.
        return []
