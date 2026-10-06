import logging

from django.core.management.base import BaseCommand
from sqlalchemy import bindparam, create_engine, pool, text
from tom_dataproducts.models import ReducedDatum

from custom_code.utils import measured

logger = logging.getLogger(__name__)

PHOTLCO_QUERY = text(
    'SELECT id, filename, filetype, difftype, filter, mag, dmag, telescope, instrument, '
    'exptime, fwhm, wcs, psfx, psfy, psfmag, psfdmag, apmag, dapmag FROM photlco WHERE id IN :ids'
).bindparams(bindparam('ids', expanding=True))


def photlco_value(row):
    value = {
        'magnitude': measured(row.mag),
        'error': measured(row.dmag),
        'filter': row.filter,
        'telescope': row.telescope,
        'instrument': row.instrument,
        'basename': row.filename.split('.')[0] if row.filename else None,
        'exptime': row.exptime,
        'fwhm': measured(row.fwhm),
        'wcs': None if row.wcs is None else int(row.wcs),
        'psfx': measured(row.psfx),
        'psfy': measured(row.psfy),
        'psfmag': measured(row.psfmag),
        'psfdmag': measured(row.psfdmag),
        'apmag': measured(row.apmag),
        'dapmag': measured(row.dapmag),
        'background_subtracted': row.filetype == 3,
    }
    if value['background_subtracted']:
        value['reduction_type'] = 'manual'
        value['template_source'] = 'SDSS' if row.filename and 'SDSS' in row.filename else 'LCO'
        algorithm = {0: 'Hotpants', 1: 'PyZOGY'}.get(row.difftype)
        if algorithm:
            value['subtraction_algorithm'] = algorithm
    return value


class Command(BaseCommand):
    help = ('One-off backfill: rebuild photometry ReducedDatum values (including sync_databases placeholders '
            'for failed reductions) from SNEx1 photlco, matched on value["snex_id"] (= photlco.id), '
            'then remove snex_id from the backfilled datums.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='report what would change without saving')
        parser.add_argument('--overwrite', action='store_true', help='also update datums that already have a basename')
        parser.add_argument('--batch-size', type=int, default=1000)
        parser.add_argument('--db-url', required=True, help='pipeline MySQL url, mysql+pymysql://user:password@host:port/supernova')

    def handle(self, *args, **options):
        datums = ReducedDatum.objects.filter(data_type='photometry', value__has_key='snex_id')
        if not options['overwrite']:
            datums = datums.exclude(value__has_key='basename')
        pks = list(datums.order_by('pk').values_list('pk', flat=True))
        size = options['batch_size']
        self.stdout.write(f'{len(pks)} photometry datums to backfill{" (dry run)" if options["dry_run"] else ""}')

        engine = create_engine(options['db_url'], poolclass=pool.NullPool)
        updated = placeholders = missing = 0
        with engine.connect() as conn:
            for start in range(0, len(pks), size):
                batch = list(ReducedDatum.objects.filter(pk__in=pks[start:start + size]))
                ids = sorted({rd.value['snex_id'] for rd in batch})
                rows = {row.id: row for row in conn.execute(PHOTLCO_QUERY, {'ids': ids})}

                changed = []
                for rd in batch:
                    row = rows.get(rd.value['snex_id'])
                    if row is None:
                        missing += 1
                        continue
                    placeholders += len(rd.value) == 1
                    rd.value.update(photlco_value(row))
                    del rd.value['snex_id']
                    changed.append(rd)

                if changed and not options['dry_run']:
                    ReducedDatum.objects.bulk_update(changed, ['value'])
                updated += len(changed)
                logger.info(f'Backfilled {start + len(batch)}/{len(pks)} photometry datums')

        self.stdout.write(f'{"Would update" if options["dry_run"] else "Updated"} {updated} '
                          f'({placeholders} placeholders); {missing} had no matching photlco row')
