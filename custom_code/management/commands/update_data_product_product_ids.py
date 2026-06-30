from django.core.management.base import BaseCommand
from custom_code.scripts.sync_databases import get_spec_row_from_filename
from tom_dataproducts.models import DataProduct

import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    
    help = 'Update the DataProduct table to have basenames as the product_id'

    def handle(self, *args, **kwargs):
        dps = DataProduct.objects.only('id', 'product_id').filter(data_product_type='spectroscopy')

        batch = []
        BATCH_SIZE = 500
        logger.info(f'Total DataProducts to convert: {dps.count()}')
        total = dps.count()
        for dp in dps.iterator(chunk_size=BATCH_SIZE):
            filename = dp.data.name.split('/')[-1].replace('.ascii','.fits')
            spec_row = get_spec_row_from_filename(filename)
            if spec_row:
                if spec_row.original:
                    bname = spec_row.original.split('.')[0]
                else:
                    bname = spec_row.filename
                spec_filepath = "/".join(spec_row.filepath.split('/')[3:]) + spec_row.filename.replace('ascii', 'fits')
                dp.product_id = bname
                dp.data.name = spec_filepath
                batch.append(dp)
            
            if len(batch) >= BATCH_SIZE:
                total -= len(batch)
                DataProduct.objects.bulk_update(batch, ['product_id', 'data'])
                batch.clear()
                logger.info(f'Batch updated, {total} remaining')

        if batch:
            DataProduct.objects.bulk_update(batch, ['product_id', 'data'])
