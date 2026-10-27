import json
import logging
import time
from datetime import datetime, timedelta

from astropy.time import Time
from django.core.management.base import BaseCommand
from tom_dataservices.data_services.tns import TNSDataService

from custom_code.models import TNSTarget
from custom_code.utils import tess_sectors

logger = logging.getLogger(__name__)

SEARCH_WINDOW = timedelta(hours=1)
TESS_LEAD_DAYS = 14
TESS_FOLLOW_DAYS = 60
TNS_PAUSE_SECONDS = 10


class Command(BaseCommand):

    help = 'Ingests TNS Targets into SNEx'

    def handle(self, *args, **kwargs):
        service = TNSDataService()
        since = (datetime.utcnow() - SEARCH_WINDOW).strftime('%Y-%m-%d %H:%M:%S')
        recent = service.query_service(service.build_query_parameters({'min_date': since}), url=service.get_urls('search_url'))
        if not recent:
            logger.info('No TNS targets found, have a good day!')
            return

        for obj in recent:
            if TNSTarget.objects.filter(name=obj['objname']).exists():
                logger.info(f'{obj["objname"]} already ingested, skipping')
                continue

            query = {'api_key': service.get_credentials(),
                     'data': json.dumps({'objname': obj['objname'], 'photometry': 1, 'spectra': 0})}
            obj_data = service.query_service(query, url=service.get_urls('object_url'))
            logger.info(f'Ingesting {obj_data["objname"]} . . .')

            try:
                disc_jd = Time(obj_data['discoverydate'], format='iso', scale='utc').jd
            except (KeyError, TypeError, ValueError):
                disc_jd = None
            photometry = obj_data.get('photometry') or []
            nondetections = [observation for observation in photometry
                             if 'Last non detection' in (observation.get('remarks') or '')
                             and disc_jd and observation['jd'] < disc_jd]
            last_nondetection = max(nondetections, key=lambda observation: observation['jd'], default=None)

            sectors = []
            if obj_data.get('radeg') is not None and obj_data.get('decdeg') is not None:
                reference_jd = disc_jd or Time.now().jd
                sectors = [found['sector'] for found in tess_sectors(float(obj_data['radeg']), float(obj_data['decdeg']))
                           if reference_jd - TESS_LEAD_DAYS <= found['mid_jd'] <= reference_jd + TESS_FOLLOW_DAYS]

            TNSTarget.objects.create(
                name=obj_data['objname'],
                name_prefix=obj_data.get('name_prefix'),
                ra=obj_data.get('radeg'),
                dec=obj_data.get('decdeg'),
                internal_name=(obj_data.get('internal_names') or '')[:100],
                source_group=(obj_data.get('reporting_group') or {}).get('group_name'),
                lnd_jd=last_nondetection['jd'] if last_nondetection else None,
                lnd_maglim=last_nondetection['limflux'] if last_nondetection else None,
                lnd_filter=last_nondetection['filters']['name'] if last_nondetection else None,
                disc_jd=disc_jd,
                disc_mag=obj_data.get('discoverymag'),
                disc_filter=(obj_data.get('discmagfilter') or {}).get('name'),
                all_phot=json.dumps({str(i): observation for i, observation in enumerate(photometry)}),
                redshift=obj_data.get('redshift'),
                classification=(obj_data.get('object_type') or {}).get('name'),
                TESS_sectors=', '.join(str(sector) for sector in sectors),
            )

            time.sleep(TNS_PAUSE_SECONDS)
