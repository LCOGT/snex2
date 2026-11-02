import json

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction
from django_comments.models import Comment
from tom_dataproducts.models import DataProduct, SpectroscopyReducedDatum

from custom_code.utils import default_version


class Command(BaseCommand):
    help = 'Moves spectrum comments from data products onto the reduction versions they were written about.'

    def add_arguments(self, parser):
        parser.add_argument('mapping', help='JSON file of {comment id: [spectrum ids]}')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        with open(options['mapping']) as f:
            mapping = {int(comment_id): pks for comment_id, pks in json.load(f).items()}
        product_type = ContentType.objects.get_for_model(DataProduct)
        spectrum_type = ContentType.objects.get_for_model(SpectroscopyReducedDatum)
        spectrum_products = dict(SpectroscopyReducedDatum.objects.filter(
            pk__in={pk for pks in mapping.values() for pk in pks}).values_list('pk', 'data_product_id'))
        moved, copied, by_default, left = 0, 0, 0, []
        with transaction.atomic():
            for comment in Comment.objects.filter(content_type=product_type).order_by('pk'):
                product_id = int(comment.object_pk) if comment.object_pk.isdigit() else None
                targets = [pk for pk in mapping.get(comment.pk, []) if spectrum_products.get(pk) == product_id]
                if not targets:
                    versions = list(SpectroscopyReducedDatum.objects.filter(data_product_id=product_id))
                    if not versions:
                        left.append(comment.pk)
                        continue
                    targets = [default_version(versions).pk]
                    by_default += 1
                else:
                    moved += 1
                Comment.objects.filter(pk=comment.pk).update(content_type=spectrum_type, object_pk=str(targets[0]))
                for pk in targets[1:]:
                    comment.pk = None
                    comment.content_type = spectrum_type
                    comment.object_pk = str(pk)
                    comment.save()
                    copied += 1
            if options['dry_run']:
                transaction.set_rollback(True)
        self.stdout.write(f'{"Would move" if options["dry_run"] else "Moved"} {moved} comments using the mapping, '
                          f'{by_default} to the default version, {copied} copied onto identical versions; '
                          f'{len(left)} left on data products without spectra: {left}')
