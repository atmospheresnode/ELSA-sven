# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2023-03-24 20:35
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0009_auto_20221214_0909'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='instrument_host',
            name='investigations',
        ),
    ]