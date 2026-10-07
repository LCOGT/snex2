import json

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction
from guardian.models import GroupObjectPermission, UserObjectPermission
from tom_dataproducts.models import PhotometryReducedDatum, ReducedDatum
from tom_targets.models import Target

from custom_code.utils import photometry_reduction_version, without_sentinels

BATCH_SIZE = 1000
OLD_TYPES = ('photometry', 'photometric_standard')


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Command(BaseCommand):
    help = ('Moves photometry and photometric_standard ReducedDatum rows into PhotometryReducedDatum, keeping the '
            'extra value keys and the per-datum permissions. Use this instead of migrate_reduced_datums.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report what would be moved without writing.')

    def build(self, rd, seen):
        value = json.loads(rd.value) if isinstance(rd.value, str) else rd.value
        if not isinstance(value, dict):
            return None, False
        value = without_sentinels(value)
        brightness = value.pop('magnitude', None)
        error = value.pop('error', None)
        limit = _number(value.pop('limit', None))
        bandpass = str(value.pop('filter', '') or '')[:32]
        instrument = str(value.pop('instrument', '') or rd.instrument)
        version = photometry_reduction_version(value, rd.data_product_id)
        measured = brightness is not None or limit is not None
        identical = (bandpass, rd.timestamp, brightness, error, limit, instrument, version if value.get('basename') else None)
        constrained = (bandpass, rd.timestamp, brightness, limit, instrument, version)
        if measured and identical in seen:
            return seen[identical], True
        if measured and constrained in seen:
            self.superseded += 1
            return seen[constrained], True
        datum = PhotometryReducedDatum(
            target_id=rd.target_id, data_product_id=rd.data_product_id, timestamp=rd.timestamp,
            source_name=rd.source_name, source_location=rd.source_location,
            telescope=str(value.pop('telescope', '') or rd.telescope), instrument=instrument,
            brightness=brightness, brightness_error=error, limit=limit,
            bandpass=bandpass, reduction_version=version, value=value)
        seen[identical] = seen[constrained] = datum
        return datum, False

    def copy_permissions(self, model, old_type, new_type, permissions, new_pks):
        copies = []
        for perm in model.objects.filter(content_type=old_type, object_pk__in=[str(pk) for pk in new_pks]):
            holder = {'group_id': perm.group_id} if model is GroupObjectPermission else {'user_id': perm.user_id}
            copies.append(model(content_type=new_type, object_pk=str(new_pks[int(perm.object_pk)]),
                                permission=permissions[perm.permission_id], **holder))
        model.objects.bulk_create(copies, ignore_conflicts=True)
        model.objects.filter(content_type=old_type, object_pk__in=[str(pk) for pk in new_pks]).delete()
        return len(copies)

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        old_type = ContentType.objects.get_for_model(ReducedDatum)
        new_type = ContentType.objects.get_for_model(PhotometryReducedDatum)
        permissions = {
            Permission.objects.get(content_type=old_type, codename=f'{action}_reduceddatum').pk:
            Permission.objects.get(content_type=new_type, codename=f'{action}_photometryreduceddatum')
            for action in ('add', 'change', 'delete', 'view')}

        rows = ReducedDatum.objects.filter(data_type__in=OLD_TYPES)
        standards = Target.objects.filter(standard=True)
        mismatched = rows.filter(data_type='photometric_standard').exclude(target__in=standards).count() + rows.filter(
            data_type='photometry', target__in=standards).count()
        pks = list(rows.order_by('target_id', '-pk').values_list('pk', flat=True))
        self.stdout.write(f'{len(pks)} photometry rows to move.')
        if mismatched:
            self.stdout.write(self.style.WARNING(
                f'{mismatched} rows have a data type that disagrees with their target\'s standard flag; '
                'after the move the target flag decides.'))

        self.superseded, moved, dropped, skipped, copied = 0, 0, 0, 0, 0
        seen, current_target = {}, None
        for start in range(0, len(pks), BATCH_SIZE):
            batch = pks[start:start + BATCH_SIZE]
            datums, old_pks, kept = [], [], {}
            for rd in sorted(ReducedDatum.objects.filter(pk__in=batch), key=lambda rd: (rd.target_id, -rd.pk)):
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
            moved += len(datums)
            dropped += len(kept)
            if dry_run:
                continue
            with transaction.atomic():
                created = PhotometryReducedDatum.objects.bulk_create(datums)
                new_pks = {old: new.pk for old, new in zip(old_pks, created)}
                new_pks.update({old: datum.pk for old, datum in kept.items()})
                for model in (GroupObjectPermission, UserObjectPermission):
                    copied += self.copy_permissions(model, old_type, new_type, permissions, new_pks)
                ReducedDatum.objects.filter(pk__in=list(new_pks)).delete()

        label = 'Would move' if dry_run else 'Moved'
        self.stdout.write(f'{label} {moved} rows; {skipped} left in place because their value is not a dictionary.')
        self.stdout.write(f'{dropped} duplicates {"would be" if dry_run else "were"} dropped, keeping the newest copy '
                          f'and its permissions plus theirs; {self.superseded} of them differed only in their error.')
        if not dry_run:
            self.stdout.write(f'Copied {copied} object permissions.')
        self.stdout.write(self.style.SUCCESS('Done.'))
