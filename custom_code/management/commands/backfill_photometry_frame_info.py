import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from sqlalchemy import bindparam, create_engine, pool, text
from tom_dataproducts.models import ReducedDatum

logger = logging.getLogger(__name__)

PHOTLCO_QUERY = text('SELECT id, filename, filter, exptime, fwhm, wcs, psfx, psfy FROM photlco WHERE id IN :ids').bindparams(
    bindparam('ids', expanding=True)
)


def _measured(value):
    return None if value is None or float(value) >= 9999 else float(value)


class Command(BaseCommand):
    help = ('One-off backfill: copy basename, filter, exptime, fwhm, wcs flag and psfx/psfy from SNEx1 photlco into '
            'photometry ReducedDatum values, matched on value["snex_id"] (= photlco.id).')

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
        updated = missing = 0
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
                    rd.value.update({
                        'basename': row.filename.split('.')[0] if row.filename else None,
                        'wcs': None if row.wcs is None else int(row.wcs),
                        'exptime': None if row.exptime is None else float(row.exptime),
                        'fwhm': _measured(row.fwhm),  # arcsec
                        'psfx': _measured(row.psfx),
                        'psfy': _measured(row.psfy),
                    })
                    if row.filter and not rd.value.get('filter'):
                        rd.value['filter'] = row.filter  # failed reductions were synced without one
                    changed.append(rd)

                if changed and not options['dry_run']:
                    ReducedDatum.objects.bulk_update(changed, ['value'])
                updated += len(changed)
                logger.info(f'Backfilled {start + len(batch)}/{len(pks)} photometry datums')

        self.stdout.write(f'{"Would update" if options["dry_run"] else "Updated"} {updated}; '
                          f'{missing} had no matching photlco row')
