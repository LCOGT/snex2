import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from sqlalchemy import bindparam, create_engine, pool, text
from tom_dataproducts.models import ReducedDatum

logger = logging.getLogger(__name__)

PHOTLCO_QUERY = text(
    'SELECT id, filename, filetype, difftype, filter, mag, dmag, telescope, instrument, '
    'exptime, fwhm, wcs, psfx, psfy FROM photlco WHERE id IN :ids'
).bindparams(bindparam('ids', expanding=True))


def _number(value):
    return None if value is None else float(value)


def photlco_value(row):
    """
    ReducedDatum value for a photlco row, in the structure the pipeline posts. Failed reductions
    keep photlco's 9999 values; sync_databases stored those as {'snex_id': id} placeholders.
    """
    value = {
        'snex_id': int(row.id),
        'magnitude': _number(row.mag),
        'error': _number(row.dmag),
        'filter': row.filter,
        'telescope': row.telescope,
        'instrument': row.instrument,
        'basename': row.filename.split('.')[0] if row.filename else None,
        'exptime': _number(row.exptime),
        'fwhm': _number(row.fwhm),  # arcsec
        'wcs': None if row.wcs is None else int(row.wcs),
        'psfx': _number(row.psfx),
        'psfy': _number(row.psfy),
        'background_subtracted': row.filetype is not None and int(row.filetype) == 3,
    }
    if value['background_subtracted']:
        value['reduction_type'] = 'manual'
        value['template_source'] = 'SDSS' if row.filename and 'SDSS' in row.filename else 'LCO'
        algorithm = {0: 'Hotpants', 1: 'PyZOGY'}.get(None if row.difftype is None else int(row.difftype))
        if algorithm:
            value['subtraction_algorithm'] = algorithm
    return value


class Command(BaseCommand):
    help = ('One-off backfill: rebuild photometry ReducedDatum values (including sync_databases placeholders '
            'for failed reductions) from SNEx1 photlco, matched on value["snex_id"] (= photlco.id).')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='report what would change without saving')
        parser.add_argument('--overwrite', action='store_true', help='also update datums that already have a basename')
        parser.add_argument('--batch-size', type=int, default=1000)

    def handle(self, *args, **options):
        datums = ReducedDatum.objects.filter(data_type='photometry', value__has_key='snex_id')
        if not options['overwrite']:
            datums = datums.exclude(value__has_key='basename')
        pks = list(datums.order_by('pk').values_list('pk', flat=True))
        size = options['batch_size']
        self.stdout.write(f'{len(pks)} photometry datums to backfill{" (dry run)" if options["dry_run"] else ""}')

        engine = create_engine(settings.SNEX1_DB_URL, poolclass=pool.NullPool)
        updated = placeholders = missing = 0
        with engine.connect() as conn:
            for start in range(0, len(pks), size):
                batch = list(ReducedDatum.objects.filter(pk__in=pks[start:start + size]))
                ids = sorted({int(rd.value['snex_id']) for rd in batch})
                rows = {row.id: row for row in conn.execute(PHOTLCO_QUERY, {'ids': ids})}

                changed = []
                for rd in batch:
                    row = rows.get(int(rd.value['snex_id']))
                    if row is None:
                        missing += 1
                        continue
                    placeholders += len(rd.value) == 1
                    rd.value.update(photlco_value(row))
                    changed.append(rd)

                if changed and not options['dry_run']:
                    ReducedDatum.objects.bulk_update(changed, ['value'])
                updated += len(changed)
                logger.info(f'Backfilled {start + len(batch)}/{len(pks)} photometry datums')

        self.stdout.write(f'{"Would update" if options["dry_run"] else "Updated"} {updated} '
                          f'({placeholders} placeholders); {missing} had no matching photlco row')
