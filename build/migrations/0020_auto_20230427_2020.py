# -*- coding: utf-8 -*-
# Generated by Django 1.11.27 on 2023-04-28 02:20
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0019_auto_20230420_1928'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='investigation',
            options={'ordering': ('name',)},
        ),
    ]
