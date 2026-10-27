from django.db import migrations

TABLE = 'tom_dataproducts_spectroscopyreduceddatum'

RENAMES = {
    'SN Ia 02ic-like': 'SN Ia-CSM',
    'SN Ia 02cx-like': 'SN Iax[02cx-like]',
    'SN Ia 91T-like': 'SN Ia-91T-like',
    'SN Ia 91bg-like': 'SN Ia-91bg-like',
    'SN Ia pec': 'SN Ia-pec',
}


def rename(apps, renames):
    SNExTarget = apps.get_model('custom_code', 'SNExTarget')
    for old, new in renames.items():
        for suffix in ('', '?'):
            SNExTarget.objects.filter(classification=old + suffix).update(classification=new + suffix)


def forwards(apps, schema_editor):
    rename(apps, RENAMES)


def backwards(apps, schema_editor):
    rename(apps, {new: old for old, new in RENAMES.items()})


class Migration(migrations.Migration):

    dependencies = [
        ('custom_code', '0023_papers_created_by'),
        ('tom_dataproducts', '0020_remove_astrometryreduceddatum_unique_astrometry_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[f'ALTER TABLE {TABLE} DROP CONSTRAINT unique_spectroscopy',
                 f'ALTER TABLE {TABLE} ADD COLUMN flux_md5 text GENERATED ALWAYS AS (md5(flux::text)) STORED',
                 f'ALTER TABLE {TABLE} ADD CONSTRAINT unique_spectroscopy '
                 'UNIQUE (target_id, "timestamp", telescope, instrument, flux_md5, reduction_version)'],
            reverse_sql=[f'ALTER TABLE {TABLE} DROP CONSTRAINT unique_spectroscopy',
                         f'ALTER TABLE {TABLE} DROP COLUMN flux_md5',
                         f'ALTER TABLE {TABLE} ADD CONSTRAINT unique_spectroscopy '
                         'UNIQUE (target_id, "timestamp", telescope, instrument, flux, reduction_version)'],
        ),
        migrations.RunPython(forwards, backwards),
    ]
