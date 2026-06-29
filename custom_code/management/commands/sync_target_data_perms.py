from itertools import islice
from collections import defaultdict
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.contenttypes.models import ContentType
from guardian.models import GroupObjectPermission
from guardian.shortcuts import assign_perm

from tom_targets.models import Target
from tom_dataproducts.models import ReducedDatum, DataProduct
from custom_code.models import ReducedDatumExtra

ACTIONS = ('view', 'change', 'delete')

MODELS = [
    (ReducedDatum,      'target', 'tom_dataproducts', 'reduceddatum'),
    (DataProduct,       'target', 'tom_dataproducts', 'dataproduct'),
    (ReducedDatumExtra, 'target', 'custom_code',      'reduceddatumextra'),
]

def _ct(app_label, model):
    return ContentType.objects.filter(app_label=app_label, model=model).first()


def _chunked(iterable, n):
    it = iter(iterable)
    while True:
        batch = list(islice(it, n))
        if not batch:
            return
        yield batch


class Command(BaseCommand):
    help = "Propagate target group object-perms onto RD/RDE/DP"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--resume-from', type=int, default=0,
                            help='Skip targets with pk < this (speeds up resume).')
        parser.add_argument('--limit', type=int, default=None,
                            help='Process at most N targets (testing).')
        parser.add_argument('--chunk', type=int, default=500,
                            help='Rows processed per batch (lower = less memory).')
        parser.add_argument('--progress', type=int, default=50)

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        chunk = opts['chunk']
        target_cts = [c for c in (_ct('custom_code', 'snextarget'),
                                  _ct('tom_targets', 'target')) if c]
        if not target_cts:
            self.stderr.write('No target content types found.'); return

        # Pre-resolve row content types once (not per target)
        row_cts = {mname: _ct(app, mname) for _, _, app, mname in MODELS}

        def source_perms(pk):
            """{group: {actions}} from whichever target CT grants more groups."""
            best = {}
            for ct in target_cts:
                d = defaultdict(set)
                for g in GroupObjectPermission.objects.filter(
                        content_type=ct, object_pk=str(pk)
                ).select_related('group', 'permission'):
                    act = g.permission.codename.split('_')[0]
                    if act in ACTIONS:
                        d[g.group].add(act)
                if len(d) > len(best):
                    best = dict(d)
            return best

        grand = defaultdict(int)
        processed = skipped = 0

        qs = Target.objects.filter(pk__gte=opts['resume_from']).order_by('pk')
        if opts['limit']:
            qs = qs[:opts['limit']]

        for i, t in enumerate(qs.iterator(), 1):
            src = source_perms(t.pk)
            if not src:
                if i % opts['progress'] == 0:
                    self.stdout.write(f'... {i} seen | {processed} updated | {skipped} skipped')
                continue

            made_change = False
            for model, fk, app, mname in MODELS:
                ct = row_cts[mname]
                # Stream row pks; never materialize the whole target's rows at once.
                row_pks = model.objects.filter(**{fk: t}).values_list('pk', flat=True)
                for batch in _chunked(row_pks.iterator(chunk_size=chunk), chunk):
                    strs = [str(p) for p in batch]
                    existing = set(
                        GroupObjectPermission.objects.filter(
                            content_type=ct, object_pk__in=strs
                        ).values_list('group_id', 'permission__codename', 'object_pk')
                    )
                    for group, acts in src.items():
                        for a in acts:
                            codename = f'{a}_{mname}'
                            missing = [p for p in batch
                                       if (group.id, codename, str(p)) not in existing]
                            if not missing:
                                continue
                            if not dry:
                                with transaction.atomic():   # one txn per (chunk, group, action)
                                    assign_perm(f'{app}.{codename}', group,
                                                model.objects.filter(pk__in=missing))
                            grand[f'{app}.{codename}'] += len(missing)
                            made_change = True
                    del existing  # release before next chunk

            if made_change:
                processed += 1
            else:
                skipped += 1

            if i % opts['progress'] == 0:
                self.stdout.write(f'... {i} seen | {processed} updated | {skipped} skipped')

        self.stdout.write('')
        for perm, n in sorted(grand.items()):
            self.stdout.write(f'{"[dry] " if dry else ""}{perm}: {n} grants')
        self.stdout.write(f'\nTotals: {processed} updated, {skipped} already-correct.')