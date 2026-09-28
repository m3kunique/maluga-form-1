from django.db import models


class FormConfig(models.Model):
    slug = models.CharField(max_length=100, unique=True, db_index=True)
    title = models.CharField(max_length=255, blank=True, default='')
    spreadsheet_id = models.CharField(max_length=255, blank=True, default='')
    sheet_name = models.CharField(max_length=100, default='Лист1')
    secret_token = models.CharField(max_length=255, blank=True, default='')
    columns_order = models.JSONField(default=list, blank=True)
    auto_init = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['slug']

    def __str__(self):
        return f"{self.title or self.slug} ({self.slug})"


class WebhookSubmission(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
        ('DUPLICATE', 'Duplicate'),
    ]

    form = models.ForeignKey(
        FormConfig,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='submissions',
    )
    form_slug = models.CharField(max_length=100, db_index=True)
    submission_id = models.CharField(max_length=100, blank=True, default='', db_index=True)
    raw_payload = models.JSONField(default=dict)
    parsed_row = models.JSONField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='PENDING',
        db_index=True,
    )
    attempts = models.IntegerField(default=0)
    last_error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"[{self.status}] {self.form_slug} #{self.id} ({self.submission_id})"


class FormLog(models.Model):
    LEVEL_CHOICES = [
        ('INFO', 'Info'),
        ('WARNING', 'Warning'),
        ('ERROR', 'Error'),
    ]

    form_slug = models.CharField(max_length=100, db_index=True)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default='INFO')
    event = models.CharField(max_length=50)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.level}] {self.form_slug} - {self.event}: {self.message[:50]}"
