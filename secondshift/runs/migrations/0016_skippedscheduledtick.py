import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0012_job_setup'),
        ('runs', '0015_run_failure_setup'),
    ]

    operations = [
        migrations.CreateModel(
            name='SkippedScheduledTick',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('scheduled_for', models.DateTimeField()),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='jobs.job')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('job', 'scheduled_for'), name='unique_job_skipped_tick')],
            },
        ),
    ]
