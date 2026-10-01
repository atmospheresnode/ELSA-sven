"""Join the two histories that forked at 0074.

validate-integration went 0075_fix_ama_investigation_lid -> 0076 -> 0077 -> 0078, and
main went 0075_product_document_doi. Both deal with Product_Document.doi: main made it
a model field again, which production has applied; 0078 on this side is now a no-op.
Nothing to do here but declare both as parents.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0075_product_document_doi'),
        ('build', '0078_repair_orphan_document_doi'),
    ]

    operations = []
