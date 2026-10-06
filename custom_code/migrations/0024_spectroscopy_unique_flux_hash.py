from django.db import migrations

TABLE = 'tom_dataproducts_spectroscopyreduceddatum'


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
    ]
