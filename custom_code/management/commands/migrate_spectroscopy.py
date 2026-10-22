import json

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django_comments.models import Comment
from guardian.models import GroupObjectPermission, UserObjectPermission
from tom_dataproducts.models import DataProduct, ReducedDatum, SpectroscopyReducedDatum

from custom_code.management.commands.migrate_photometry import Command as PhotometryCommand
from custom_code.models import ReducedDatumExtra
from custom_code.raw_spectra import is_floyds_frame
from custom_code.utils import file_version, frame_night, measured, upload_reduction_version

BATCH_SIZE = 100
SPECTRUM_KEYS = ('flux', 'photon_flux', 'wavelength', 'error', 'flux_error', 'flux_units', 'photon_flux_units',
                 'wavelength_units', 'telescope', 'instrument')


def _same_coverage(a, b):
    shared = min(a[1], b[1]) - max(a[0], b[0])
    longest = max(a[1] - a[0], b[1] - b[0])
    return longest > 0 and shared / longest > 0.8


def _floats(values):
    try:
        return [float(value) for value in values]
    except (TypeError, ValueError):
        return []


class Command(PhotometryCommand):
    help = ('Moves spectroscopy ReducedDatum rows into SpectroscopyReducedDatum, converting every stored spectrum '
            'shape to wavelength and flux arrays and keeping per-datum permissions. Comments move to the data product.')

    def build(self, rd, seen):
        value = json.loads(rd.value) if isinstance(rd.value, str) else rd.value
        if not isinstance(value, dict):
            return None, False
        if value.get('photon_flux') or value.get('flux'):
            wavelength = _floats(value.get('wavelength') or [])
            flux = _floats(value.get('photon_flux') or value.get('flux'))
            flux_unit = value.get('photon_flux_units') or value.get('flux_units') or ''
            extras = {key: item for key, item in value.items() if key not in SPECTRUM_KEYS}
        else:
            points = [point for _, point in sorted(value.items(), key=lambda item: int(item[0]) if item[0].isdigit() else 0)
                      if isinstance(point, dict) and 'wavelength' in point and 'flux' in point]
            wavelength = _floats([point['wavelength'] for point in points])
            flux = _floats([point['flux'] for point in points])
            flux_unit, extras = '', {}
        if not flux or len(wavelength) != len(flux):
            return None, False
        version = self.version(rd.data_product) if rd.data_product_id else ''
        upload = ReducedDatumExtra.objects.filter(data_product_id=rd.data_product_id).first() if rd.data_product_id else None
        facts = (upload.value or {}) if upload else {}
        extras.update({key: facts[key] for key in ('reducer', 'final_reduction') if facts.get(key)})
        key = (rd.timestamp, tuple(flux), version)
        if key in seen:
            return seen[key], True
        seen[key] = SpectroscopyReducedDatum(
            target_id=rd.target_id, data_product_id=rd.data_product_id, timestamp=rd.timestamp,
            source_name=rd.source_name, source_location=rd.source_location,
            telescope=str(value.get('telescope') or facts.get('telescope') or rd.telescope),
            instrument=str(value.get('instrument') or facts.get('instrument') or rd.instrument or self.camera(rd.data_product)),
            exposure_time=measured(facts.get('exptime')),
            wavelength=wavelength, flux=flux, error=_floats(value.get('error') or value.get('flux_error') or []),
            flux_unit=str(flux_unit), wavelength_unit=str(value.get('wavelength_units') or ''),
            reduction_version=version, value=extras)
        return seen[key], False

    def camera(self, product):
        product_id = product.product_id if product else ''
        return product_id.split('-')[1] if frame_night(product_id) else ''

    def find_merges(self, observations):
        products = {dp.pk: dp for dp in DataProduct.objects.filter(pk__in=list(observations))}
        groups = {}
        for pk, (target_id, timestamp, instrument, span) in observations.items():
            if pk in products:
                name = f'{products[pk].product_id or ""} {products[pk].data.name or ""}'.lower()
                groups.setdefault((target_id, timestamp, 'host' in name), []).append(pk)
        merges = []
        for pks in groups.values():
            pks.sort(key=lambda pk: (not is_floyds_frame(products[pk].product_id), pk))
            clusters = []
            for pk in pks:
                instrument, span = observations[pk][2], observations[pk][3]
                for cluster in clusters:
                    lead = observations[cluster[0]]
                    if (not instrument or not lead[2] or instrument == lead[2]) and _same_coverage(span, lead[3]):
                        cluster.append(pk)
                        break
                else:
                    clusters.append([pk])
            merges.extend((cluster[0], cluster[1:]) for cluster in clusters if len(cluster) > 1)
        return merges

    def describe(self, pk):
        dp = DataProduct.objects.select_related('target').get(pk=pk)
        return f'{dp.pk} ({dp.target.name}, {dp.product_id or dp.data.name.split("/")[-1]})'

    def merge(self, survivor, others, product_type):
        others = [pk for pk in others if not ReducedDatum.objects.filter(data_product_id=pk).exists()]
        with transaction.atomic():
            SpectroscopyReducedDatum.objects.filter(data_product_id__in=others).update(data_product_id=survivor)
            Comment.objects.filter(content_type=product_type, object_pk__in=[str(pk) for pk in others]).update(
                object_pk=str(survivor))
            ReducedDatumExtra.objects.filter(data_product_id__in=others).delete()
            DataProduct.objects.filter(pk__in=others).delete()

    def version(self, product):
        try:
            return file_version(product.data)
        except (OSError, ValueError):
            return upload_reduction_version(product.pk)

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        old_type = ContentType.objects.get_for_model(ReducedDatum)
        new_type = ContentType.objects.get_for_model(SpectroscopyReducedDatum)
        product_type = ContentType.objects.get_for_model(DataProduct)
        permissions = {
            Permission.objects.get(content_type=old_type, codename=f'{action}_reduceddatum').pk:
            Permission.objects.get(content_type=new_type, codename=f'{action}_spectroscopyreduceddatum')
            for action in ('add', 'change', 'delete', 'view')}

        pks = list(ReducedDatum.objects.filter(data_type='spectroscopy').order_by('target_id', '-pk').values_list('pk', flat=True))
        self.stdout.write(f'{len(pks)} spectra to move.')

        moved, dropped, skipped, copied, comments = 0, 0, 0, 0, 0
        seen, current_target, observations = {}, None, {}
        for start in range(0, len(pks), BATCH_SIZE):
            datums, old_pks, kept = [], [], {}
            for rd in sorted(ReducedDatum.objects.filter(pk__in=pks[start:start + BATCH_SIZE]), key=lambda rd: (rd.target_id, -rd.pk)):
                if rd.target_id != current_target:
                    seen, current_target = {}, rd.target_id
                datum, duplicate = self.build(rd, seen)
                if datum is None:
                    skipped += 1
                elif duplicate:
                    kept[rd.pk] = datum
                else:
                    datums.append(datum)
                    old_pks.append(rd.pk)
                    if datum.data_product_id and datum.wavelength:
                        observations.setdefault(datum.data_product_id, (
                            datum.target_id, datum.timestamp, datum.instrument,
                            (min(datum.wavelength), max(datum.wavelength))))
            moved += len(datums)
            dropped += len(kept)
            if dry_run:
                continue
            with transaction.atomic():
                created = SpectroscopyReducedDatum.objects.bulk_create(datums)
                new_pks = {old: new.pk for old, new in zip(old_pks, created)}
                new_pks.update({old: datum.pk for old, datum in kept.items()})
                for model in (GroupObjectPermission, UserObjectPermission):
                    copied += self.copy_permissions(model, old_type, new_type, permissions, new_pks)
                products = dict(SpectroscopyReducedDatum.objects.filter(pk__in=new_pks.values()).exclude(
                    data_product=None).values_list('pk', 'data_product_id'))
                for old, new in new_pks.items():
                    moved_to = {'content_type': product_type, 'object_pk': str(products[new])} if new in products else {
                        'content_type': new_type, 'object_pk': str(new)}
                    comments += Comment.objects.filter(content_type=old_type, object_pk=str(old)).update(**moved_to)
                ReducedDatum.objects.filter(pk__in=list(new_pks)).delete()

        merges = self.find_merges(observations)
        for survivor, others in merges:
            self.stdout.write(f'{"Would merge" if dry_run else "Merged"} {", ".join(self.describe(pk) for pk in others)} '
                              f'into {self.describe(survivor)} as versions')
            if not dry_run:
                self.merge(survivor, others, product_type)

        label = 'Would move' if dry_run else 'Moved'
        self.stdout.write(f'{label} {moved} spectra; {skipped} left in place because no spectrum could be read from them.')
        self.stdout.write(f'{len(merges)} observations {"would be" if dry_run else "were"} merged into single data products '
                          f'with {sum(len(others) for _, others in merges)} extra versions.')
        self.stdout.write(f'{dropped} duplicates {"would be" if dry_run else "were"} dropped, keeping the newest copy.')
        if not dry_run:
            self.stdout.write(f'Copied {copied} object permissions and moved {comments} comments.')
        self.stdout.write(self.style.SUCCESS('Done.'))
