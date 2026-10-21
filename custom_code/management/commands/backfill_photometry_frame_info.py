import json
import logging
import os

from django.contrib.auth.models import Group
from django.core.files import File
from django.core.management.base import BaseCommand
from django.db import transaction
from guardian.shortcuts import assign_perm
from sqlalchemy import bindparam, create_engine, pool, text
from tom_dataproducts.models import DataProduct, ReducedDatum
from tom_targets.models import Target, TargetName

from custom_code.match_managers import MATCH_RADIUS_ARCSEC
from custom_code.utils import measured, set_dataproduct_view_groups

logger = logging.getLogger(__name__)

PHOTLCO_QUERY = text(
    'SELECT id, filename, filetype, difftype, filter, mag, dmag, telescope, instrument, '
    'exptime, fwhm, wcs, psfx, psfy, psfmag, psfdmag, apmag, dapmag FROM photlco WHERE id IN :ids'
).bindparams(bindparam('ids', expanding=True))


DEFAULT_GROUPIDCODE = 1703768065789

MISSED_SPECTRA_QUERY = text(
    'SELECT raw.targetid, raw.filepath, raw.filename, raw.dateobs, raw.propid, raw.groupidcode FROM speclcoraw AS raw '
    'WHERE raw.type = "SPECTRUM" AND raw.filename LIKE "%-e00.fits" AND COALESCE(raw.markedasbad, 0) = 0 '
    'AND COALESCE(raw.propid, "") != "FLOYDS standards" AND raw.filepath NOT LIKE "%bad%" '
    'AND NOT EXISTS (SELECT 1 FROM spec WHERE spec.original = raw.filename) AND raw.dayobs >= :since '
    'ORDER BY raw.dayobs'
)
PIPELINE_TARGETS_QUERY = text(
    'SELECT targets.id, targets.ra0, targets.dec0, targets.groupidcode, classifications.name AS classification '
    'FROM targets LEFT JOIN classifications ON classifications.id = targets.classificationid WHERE targets.id IN :ids'
).bindparams(bindparam('ids', expanding=True))
PIPELINE_NAMES_QUERY = text(
    'SELECT targetid, name FROM targetnames WHERE targetid IN :ids ORDER BY id'
).bindparams(bindparam('ids', expanding=True))
PIPELINE_GROUPS_QUERY = text('SELECT name, idcode FROM `groups`')


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
        value['template_source'] = next((source for source in ('SDSS', 'PS1') if source in (row.filename or '')), 'LCO')
        algorithm = {0: 'Hotpants', 1: 'PyZOGY'}.get(row.difftype)
        if algorithm:
            value['subtraction_algorithm'] = algorithm
    return value


def groups_for(groupidcode, pipeline_groups):
    groupidcode = int(groupidcode or DEFAULT_GROUPIDCODE)
    names = [name for name, idcode in pipeline_groups if idcode and int(idcode) & groupidcode == int(idcode)]
    return list(Group.objects.filter(name__in=names))


def target_name_lookup():
    lookup = {}
    for target in Target.objects.prefetch_related('aliases'):
        for name in target.names:
            lookup.setdefault(Target.matches.simplify_name(name), target)
    return lookup


def find_or_create_target(names, row, groups, lookup, create=True):
    for name in names:
        target = lookup.get(Target.matches.simplify_name(name))
        if target:
            return target, False
    if row.ra0 is None or row.dec0 is None:
        return None, False
    target = Target.matches.match_cone_search(float(row.ra0), float(row.dec0), MATCH_RADIUS_ARCSEC).order_by('separation').first()
    if target:
        return target, False
    if not create:
        return None, True
    unique_names, seen = [], set()
    for name in names:
        if Target.matches.simplify_name(name) not in seen:
            seen.add(Target.matches.simplify_name(name))
            unique_names.append(name)
    with transaction.atomic():
        target = Target.objects.create(name=unique_names[0], ra=float(row.ra0), dec=float(row.dec0),
                                       type='SIDEREAL', permissions='PRIVATE')
        for name in unique_names[1:]:
            TargetName.objects.create(target=target, name=name)
        for group in groups:
            assign_perm('custom_code.view_target', group, target)
            assign_perm('custom_code.change_target', group, target)
            assign_perm('custom_code.delete_target', group, target)
    for name in unique_names:
        lookup[Target.matches.simplify_name(name)] = target
    return target, True


class Command(BaseCommand):
    help = ('One-off backfill: rebuild photometry ReducedDatum values (including sync_databases placeholders '
            'for failed reductions) from SNEx1 photlco, matched on value["snex_id"] (= photlco.id), '
            'then remove snex_id from the backfilled datums. With --floyds-inbox, also add unreduced FLOYDS '
            'spectra from speclcoraw to the FLOYDS inbox.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='report what would change without saving')
        parser.add_argument('--overwrite', action='store_true', help='also update datums that already have a basename')
        parser.add_argument('--batch-size', type=int, default=1000)
        parser.add_argument('--db-url', required=True, help='pipeline MySQL url, mysql+pymysql://user:password@host:port/supernova')
        parser.add_argument('--floyds-inbox', action='store_true',
                            help='also add raw FLOYDS spectra that are in speclcoraw but have no reduced spectrum to the FLOYDS inbox')
        parser.add_argument('--since', default='00000000', help='with --floyds-inbox, only spectra observed on or after this day, YYYYMMDD')

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

        if options['floyds_inbox']:
            self.backfill_floyds_inbox(engine, options['since'], options['dry_run'])

    def backfill_floyds_inbox(self, engine, since, dry_run):
        with engine.connect() as conn:
            spectra = list(conn.execute(MISSED_SPECTRA_QUERY, {'since': since}))
            ids = sorted({row.targetid for row in spectra if row.targetid is not None}) or [0]
            pipeline_targets = {row.id: row for row in conn.execute(PIPELINE_TARGETS_QUERY, {'ids': ids})}
            pipeline_names = {}
            for row in conn.execute(PIPELINE_NAMES_QUERY, {'ids': ids}):
                pipeline_names.setdefault(row.targetid, []).append(row.name)
            pipeline_groups = [(row.name, row.idcode) for row in conn.execute(PIPELINE_GROUPS_QUERY)]
        self.stdout.write(f'{len(spectra)} raw FLOYDS spectra without a reduced spectrum since {since}')

        lookup = target_name_lookup()
        resolved = {}
        added = existing = skipped = new_targets = failed = 0
        for row in spectra:
            product_id = row.filename.split('.')[0]
            pipeline_target = pipeline_targets.get(row.targetid)
            names = pipeline_names.get(row.targetid, [])
            if (pipeline_target is None or not names or pipeline_target.classification == 'Standard'
                    or any('test_' in name for name in names)):
                skipped += 1
                continue
            if DataProduct.objects.filter(product_id=product_id).exists():
                existing += 1
                continue
            try:
                if row.targetid not in resolved:
                    target_groups = groups_for(pipeline_target.groupidcode, pipeline_groups)
                    target, created = find_or_create_target(names, pipeline_target, target_groups, lookup,
                                                            create=not dry_run)
                    resolved[row.targetid] = (target, created)
                    new_targets += created
                target, created = resolved[row.targetid]
                if (target is None and not (dry_run and created)) or (target is not None and target.standard):
                    skipped += 1
                    continue
                if dry_run:
                    added += 1
                    continue
                groups = groups_for(row.groupidcode or pipeline_target.groupidcode, pipeline_groups)
                with transaction.atomic():
                    dp = DataProduct.objects.create(
                        target=target, product_id=product_id, data_product_type='raw_spectrum',
                        extra_data=json.dumps({'path': row.filepath, 'filename': row.filename,
                                               'dateobs': str(row.dateobs), 'propid': row.propid}))
                    thumbnail = os.path.join(row.filepath, row.filename.replace('.fits', '.png'))
                    if os.path.isfile(thumbnail):
                        with open(thumbnail, 'rb') as f:
                            dp.thumbnail.save(os.path.basename(thumbnail), File(f))
                    set_dataproduct_view_groups(dp, groups)
                added += 1
            except Exception as e:
                failed += 1
                logger.exception(f'Could not add {row.filename} to the FLOYDS inbox: {e}')
            if (added + failed) % 500 == 0 and added + failed:
                logger.info(f'FLOYDS inbox backfill: {added} added, {failed} failed so far')

        self.stdout.write(f'{"Would add" if dry_run else "Added"} {added} FLOYDS inbox entries '
                          f'({new_targets} {"would be " if dry_run else ""}new targets); {existing} were already in SNEx, {skipped} skipped '
                          f'(standards, test, unknown or unplaceable targets), {failed} failed')
