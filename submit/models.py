from django.db import models
from django.conf import settings

class BundleSubmission(models.Model):
    class BundleType(models.TextChoices):
        ARCHIVE = 'ARCHIVE', 'Archive Bundle'
        EXTERNAL = 'EXTERNAL', 'External Bundle'

    class StatusType(models.TextChoices):
        PROCESSING = 'PROCESSING', 'Processing'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    bundle_type = models.CharField(max_length=10, choices=BundleType.choices)
    status = models.CharField(max_length=10, choices=StatusType.choices, default=StatusType.COMPLETED)
    filename = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"{self.filename} ({self.status})"