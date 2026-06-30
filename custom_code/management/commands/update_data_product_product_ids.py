from django.core.management.base import BaseCommand
from custom_code.scripts.sync_databases import get_spec_row_from_filename, get_spec_row_from_id
from tom_dataproducts.models import DataProduct
import logging
logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Update the DataProduct table to have basenames as the product_id'

    def handle(self, *args, **kwargs):
        dps = DataProduct.objects.filter(data_product_type='spectroscopy')
        logger.info(f'Total DataProducts to convert: {dps.count()}')
        batch = []
        BATCH_SIZE = 500
        total = dps.count()
        seen_ids = set(DataProduct.objects.exclude(product_id__isnull=True).values_list('product_id', flat=True))

        for dp in dps.iterator(chunk_size=BATCH_SIZE):
            filename = dp.data.name.split('/')[-1].replace('.ascii', '.fits')
            snexid = dp.reduceddatumextra_set.first().value.get('snex_id')
            if snexid:
                spec_row = get_spec_row_from_id(snexid)
            else:
                spec_row = get_spec_row_from_filename(filename)
            if spec_row:
                if spec_row.original:
                    bname = spec_row.original.replace('.fits', '')
                else:
                    bname = spec_row.filename
                spec_filepath = "/".join(spec_row.filepath.split('/')[3:]) + spec_row.filename.replace('ascii', 'fits')

                if bname in seen_ids:
                    logger.warning(f'Skipping duplicate product_id: basename={bname}, filename={spec_filepath}')
                    continue

                seen_ids.add(bname)
                dp.product_id = bname
                dp.data.name = spec_filepath
                logger.info(f'product id: {bname}, file name: {spec_filepath}')
                batch.append(dp)

            if len(batch) >= BATCH_SIZE:
                total -= len(batch)
                DataProduct.objects.bulk_update(batch, ['product_id', 'data'])
                batch.clear()
                logger.info(f'Batch updated, {total} remaining')

        if batch:
            DataProduct.objects.bulk_update(batch, ['product_id', 'data'])