# Generated by Django 5.1.7 on 2025-03-23 19:24

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('server', '0002_question_status'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='question',
            name='status',
        ),
        migrations.AddField(
            model_name='quiz',
            name='status',
            field=models.CharField(choices=[('scheduled', 'Scheduled'), ('opened', 'Opened'), ('closed', 'Closed')], default='scheduled', max_length=20),
        ),
    ]
