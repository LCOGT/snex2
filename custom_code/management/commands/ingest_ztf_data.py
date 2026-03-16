from django.core.management.base import BaseCommand
import requests
import json
import logging
from astropy.time import Time, TimezoneInfo
from tom_dataproducts.models import ReducedDatum, DataProduct
from tom_targets.models import Target
from custom_code.models import ReducedDatumExtra
from guardian.shortcuts import assign_perm
from django.contrib.auth.models import Group

logger = logging.getLogger(__name__)


def get_ztf_data(target):
    logger.info(f'starting ztf ingestion')
    filters = {1: 'g_ZTF', 2: 'r_ZTF', 3: 'i_ZTF'}
    
    ztf_name = next((name for name in target.names if 'ZTF' in name), None)
    if not ztf_name:
        return []

    url = f'https://api.alerce.online/ztf/v1/objects/{ztf_name}/lightcurve'
    try:
        r = requests.get(url)
        r.raise_for_status()
        results = r.json()
    except Exception as e:
        logger.info('Failed to get ZTF photometry for {}: {}'.format(ztf_name, e))
        return []

    detections = results.get('detections', [])
    logger.info(f'{len(detections)} found for {target.name}')
    if not detections:
        logger.warning(f'No ZTF detections found for {ztf_name}')
        return []

    dp, created = DataProduct.objects.get_or_create(
        target = target,
        observation_record = None,
        data_product_type = 'photometry',
        product_id = f'{ztf_name}_photometry'
    )
    dp.data.name = f'{ztf_name}_photometry'
    dp.save()

    if created:
        for group in Group.objects.all():
            assign_perm('tom_dataproducts.view_dataproduct', group, dp)
            assign_perm('tom_dataproducts.change_dataproduct', group, dp)
            assign_perm('tom_dataproducts.delete_dataproduct', group, dp)
        datum_extra_value = {
            'data_product_id': dp.id,
            'instrument': 'ZTF',
            'photometry_type': 'PSF',
            'data_product_product_id': f'{ztf_name}_photometry'
        }
        rd_extra, _ = ReducedDatumExtra.objects.get_or_create(
            target = target,
            data_type = 'photometry',
            key = 'upload_extras',
            value = json.dumps(datum_extra_value)
        )

    for alert in detections:
        if alert.get('dubious'):
            continue
        required = ['mjd', 'magpsf', 'fid', 'sigmapsf']
        if not all(alert.get(key) is not None for key in required):
            continue
        if alert['fid'] not in filters:
            continue

        jd = Time(alert['mjd'], format = 'mjd', scale = 'utc')
        value = {
            'magnitude': alert['magpsf'],
            'filter': filters[alert['fid']],
            'error': alert['sigmapsf']
        }
        rd, rd_created = ReducedDatum.objects.get_or_create(
            timestamp = jd.to_datetime(timezone = TimezoneInfo()),
            value = value,
            source_name = ztf_name,
            source_location = url,
            data_type = 'photometry',
            target = target,
            data_product = dp
        )
        if rd_created:
            for group in Group.objects.all():
                assign_perm('tom_dataproducts.view_reduceddatum', group, rd)
                assign_perm('tom_dataproducts.change_reduceddatum', group, rd)
                assign_perm('tom_dataproducts.delete_reduceddatum', group, rd)

    logger.info(f'Finished ingesting ZTF photometry for {ztf_name} ({target.name}) ({len(detections)} detections)')
    return []

def delete_ztf_data(target):
    ztf_name = next((name for name in target.names if 'ZTF' in name), None)
    if not ztf_name:
        logger.warning(f'No ZTF name found for {target}')
        return
    deleted, _ = DataProduct.objects.filter(
        target = target,
        data_product_type = 'photometry',
        product_id = 'photometry_{}'.format(ztf_name)
    ).delete()
    logger.info(f'Deleted {deleted} ZTF photometry points for {target}')

class Command(BaseCommand):

    help = 'Imports ZTF photometry into SNEx2'

    def add_arguments(self, parser):
        parser.add_argument('--target_id', help = 'Ingest data for this target')
        parser.add_argument('--delete', action = 'store_true', help = 'Deletes ZTF data for this target')

    def handle(self, *args, **options):

        if options['target_id']:
            target = Target.objects.get(id = int(options['target_id']))
            
            if options['delete']:
                delete_ztf_data(target)
            
            get_ztf_data(target)
        
        else:
            target_query = Target.objects.all()
            for target in target_query:
                get_ztf_data(target)
