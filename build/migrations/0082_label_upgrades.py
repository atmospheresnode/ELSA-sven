import build.checker
import build.label_migrations
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0081_validationrun_tool_version'),
    ]

    operations = [
        # Existing bundles start at 0 so every label migration reaches them.
        migrations.AddField(
            model_name='bundle',
            name='label_revision',
            field=models.PositiveIntegerField(default=0),
        ),
        # New bundles start at the newest.
        migrations.AlterField(
            model_name='bundle',
            name='label_revision',
            field=models.PositiveIntegerField(default=build.label_migrations.head),
        ),
        # Existing runs get '' so they count as out of date once.
        migrations.AddField(
            model_name='validationrun',
            name='checker_version',
            field=models.CharField(blank=True, default='', max_length=64, null=True),
        ),
        migrations.AlterField(
            model_name='validationrun',
            name='checker_version',
            field=models.CharField(blank=True, default=build.checker.checker_version, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='citation_information',
            name='contributors_saved',
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name='Citation_Contributor',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('author', 'Author'), ('editor', 'Editor')], max_length=8)),
                ('kind', models.CharField(choices=[('person', 'Person'), ('organization', 'Organization')], max_length=12)),
                ('position', models.PositiveIntegerField(default=0)),
                ('given_name', models.CharField(blank=True, default='', max_length=255)),
                ('family_name', models.CharField(blank=True, default='', max_length=255)),
                ('orcid', models.CharField(blank=True, default='', max_length=255)),
                ('affiliation', models.CharField(blank=True, default='', max_length=255)),
                ('name', models.CharField(blank=True, default='', max_length=255)),
                ('rorid', models.CharField(blank=True, default='', max_length=255)),
                ('sequence_number', models.CharField(blank=True, default='', max_length=255)),
                ('parent_org_name', models.CharField(blank=True, default='', max_length=255)),
                ('citation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contributors', to='build.citation_information')),
            ],
            options={
                'ordering': ['role', 'kind', 'position', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='BundleUpgrade',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.PositiveIntegerField()),
                ('name', models.CharField(max_length=255)),
                ('title', models.CharField(max_length=255)),
                ('status', models.CharField(choices=[('applied', 'Applied'), ('failed', 'Failed'), ('restored', 'Undone')], default='applied', max_length=16)),
                ('changes', models.JSONField(blank=True, default=list)),
                ('files', models.JSONField(blank=True, default=list)),
                ('error', models.TextField(blank=True, default='')),
                ('source', models.CharField(blank=True, default='', max_length=16)),
                ('snapshot', models.CharField(blank=True, default='', max_length=255)),
                ('applied_at', models.DateTimeField(auto_now_add=True)),
                ('seen_at', models.DateTimeField(blank=True, null=True)),
                ('bundle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='upgrades', to='build.bundle')),
            ],
            options={
                'ordering': ['-applied_at', '-pk'],
            },
        ),
    ]
