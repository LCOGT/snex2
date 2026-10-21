from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('custom_code', '0021_snextarget_standard'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='snextarget',
            name='pipeline_id',
        ),
    ]
