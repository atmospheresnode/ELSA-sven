# Generated by Django 3.2.20 on 2023-08-25 16:45

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0024_merge_20230626_1557'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='instrument_host',
            name='targets',
        ),
    ]
