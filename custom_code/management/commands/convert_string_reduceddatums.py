import json

from django.core.management.base import BaseCommand
from tom_dataproducts.models import ReducedDatum


class Command(BaseCommand):
    help = 'Convert reduced datums whose value was stored as a JSON string into JSON objects'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report what would change without saving')

    def handle(self, *args, **options):
        converted, skipped = 0, 0
        for datum in ReducedDatum.objects.filter(data_product__isnull=False).iterator():
            if not isinstance(datum.value, str):
                continue
            try:
                value = json.loads(datum.value)
            except ValueError:
                skipped += 1
                continue
            if not isinstance(value, dict):
                skipped += 1
                continue
            converted += 1
            if not options['dry_run']:
                ReducedDatum.objects.filter(pk=datum.pk).update(value=value)
        self.stdout.write(f'{converted} converted, {skipped} could not be parsed' + (' (dry run)' if options['dry_run'] else ''))
