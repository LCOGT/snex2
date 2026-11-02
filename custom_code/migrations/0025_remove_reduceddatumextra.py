from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('custom_code', '0024_spectroscopy_flux_hash_and_classification_renames'),
    ]

    operations = [
        migrations.DeleteModel(name='ReducedDatumExtra'),
    ]
