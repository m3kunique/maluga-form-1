from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='FormConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.CharField(db_index=True, max_length=100, unique=True)),
                ('title', models.CharField(blank=True, default='', max_length=255)),
                ('spreadsheet_id', models.CharField(blank=True, default='', max_length=255)),
                ('sheet_name', models.CharField(default='Лист1', max_length=100)),
                ('secret_token', models.CharField(blank=True, default='', max_length=255)),
                ('columns_order', models.JSONField(blank=True, default=list)),
                ('auto_init', models.BooleanField(default=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['slug'],
            },
        ),
        migrations.CreateModel(
            name='FormLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('form_slug', models.CharField(db_index=True, max_length=100)),
                ('level', models.CharField(choices=[('INFO', 'Info'), ('WARNING', 'Warning'), ('ERROR', 'Error')], default='INFO', max_length=20)),
                ('event', models.CharField(max_length=50)),
                ('message', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='WebhookSubmission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('form_slug', models.CharField(db_index=True, max_length=100)),
                ('submission_id', models.CharField(blank=True, db_index=True, default='', max_length=100)),
                ('raw_payload', models.JSONField(default=dict)),
                ('parsed_row', models.JSONField(blank=True, null=True)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('SUCCESS', 'Success'), ('FAILED', 'Failed'), ('DUPLICATE', 'Duplicate')], db_index=True, default='PENDING', max_length=20)),
                ('attempts', models.IntegerField(default=0)),
                ('last_error', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('form', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='submissions', to='webhook.formconfig')),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
    ]
