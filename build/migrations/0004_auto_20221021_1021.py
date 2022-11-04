# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2022-10-21 16:21
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0003_auto_20220819_1212'),
    ]

    operations = [
        migrations.CreateModel(
            name='AdditionalCollections',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('has_data', models.BooleanField(default=True)),
                ('bundle', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='build.Bundle')),
            ],
            options={
                'verbose_name_plural': 'AdditionalCollections',
            },
        ),
        migrations.RemoveField(
            model_name='collections',
            name='has_context',
        ),
        migrations.RemoveField(
            model_name='collections',
            name='has_document',
        ),
        migrations.AlterField(
            model_name='product_collection',
            name='collection',
            field=models.CharField(choices=[('Document', 'Document'), ('Context', 'Context'), ('XML_Schema', 'XML_Schema'), ('Data', 'Data'), ('Browse', 'Browse'), ('Geometry', 'Geometry'), ('Calibration', 'Calibration'), ('Not_Set', 'Not_Set')], max_length=100),
        ),
    ]