# -*- coding: utf-8 -*-
"""Copy the citation's people from the bundle label into Citation_Contributor if they aren't
there yet, then write the citation into every label from the database.
"""

title = 'Rewrote the citation in every label from your Citation Information, so they all match'


def apply(bundle):
    from build import bundle_setup
    from build.models import Product_Bundle

    citation = bundle.citation_information_set.first()
    if citation is None:
        return []
    product_bundle = Product_Bundle.objects.filter(bundle=bundle).first()
    if product_bundle is None:
        return []
    try:
        path = product_bundle.label()
    except (AttributeError, TypeError):
        # Some old bundles have no bundleID, so there's no label path.
        return []
    import os
    if not path or not os.path.exists(path):
        return []

    if not citation.contributors_saved:
        bundle_setup.store_rows(citation, bundle_setup.rows_from_label(citation))
    bundle_setup.write_citation(citation)
    return ['citation']
