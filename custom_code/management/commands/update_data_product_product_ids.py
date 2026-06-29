from django.core.management.base import BaseCommand
from custom_code.scripts.sync_databases import get_spec_row_from_filename
from tom_dataproducts.models import DataProduct

class Command(BaseCommand):
    
    help = 'Update the DataProduct table to have basenames as the product_id'

    def handle(self, *args, **kwargs):
        dps = DataProduct.objects.only('id', 'product_id').filter(data_product_type='spectroscopy')

        batch = []
        BATCH_SIZE = 500

        for dp in dps.iterator(chunk_size=BATCH_SIZE):
            filename = dp.data.name.split('/')[-1].replace('.ascii','.fits')
            spec_row = get_spec_row_from_filename(filename)
            if spec_row:
                bname = spec_row.original.split('.')[0]
                spec_filepath = "/".join(spec_row.filepath.split('/')[3:]) + spec_row.filename.replace('ascii', 'fits')
                dp.product_id = bname
                dp.data.name = spec_filepath
                batch.append(dp)
            if len(batch) >= BATCH_SIZE:
                DataProduct.objects.bulk_update(batch, ['product_id', 'data'])
                batch.clear()

        if batch:
            DataProduct.objects.bulk_update(batch, ['product_id', 'data'])
