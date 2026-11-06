from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('staff', '0007_unassigned_preference'),
    ]

    operations = [
        migrations.AddField(
            model_name='broadcast',
            name='total',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='broadcast',
            name='cursor',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='broadcast',
            name='worker',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='broadcast',
            name='heartbeat_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
