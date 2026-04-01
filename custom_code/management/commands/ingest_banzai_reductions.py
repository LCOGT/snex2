from django.core.management.base import BaseCommand
from tom_dataproducts.models import ReducedDatum, DataProduct, ObservationRecord
from tom_targets.models import Target, TargetName
from custom_code.models import ReducedDatumExtra 
from custom_code.processors.data_processor import run_custom_data_processor
from custom_code.hooks import get_metadata
import os

import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Ingest banzai-floyds reduced spectra from the last 7 days into ReducedDatum table'

    def handle(self, *args, **kwargs):

        token = os.environ['LCO_APIKEY']
        authtoken = {'Authorization': 'Token ' + token}

        # get_metadata will take from window starting 7 days prior to now
        frames = get_metadata(
            authtoken=authtoken,
            OBSTYPE='SPECTRUM',
            basename = 'e91-1d',
            RLEVEL=2,           # redundant
            include_related_frames = False,
            public = False
        )

        for frame in frames:

            targetquery = Target.objects.filter(name=frame['target_name'])
            logger.info(f"ingest_banzai_spec targetquery, targetname: {targetquery} , {frame['target_name']}")

            if not targetquery:
                targetquery = TargetName.objects.filter(name=frame['target_name'])
                logger.info(f"targetquery in if statement: {targetquery}")
                targetid = targetquery.first().target_id
                logger.info(f"in if statement: Targetid: {targetid}")
            else:
                logger.info(f"targetquery in else statement: {targetquery}")
                targetid = targetquery.first().id
                logger.info(f"in else statement Targetid: {targetid}")


            target = Target.objects.get(id=targetid)

            observation_id = frame['observation_id']
        
            obs_record = ObservationRecord.objects.get(observation_id = observation_id) # should this be observation_id or request_id
            
            dp = DataProduct(
                        target=target,
                        observation_record=obs_record,
                        product_id=frame['id'],
                        data_product_type='spectroscopy',
                        extra_data = frame['basename']
                    )
            dp.save()
            extras = {} # only needed for photometry
            
            rdextra_value = {
                'telescope':  frame.get('TELID', ''),
                'instrument': frame.get('INSTRUME', ''),
                'site':       frame.get('SITEID', ''),
                'exptime':    frame.get('EXPTIME', ''),
                'reducer':    'Banzai-Floyds',       # fill in if known -- how to change if reduced manually??
                'airmass':    frame.get('AIRMASS', ''),
                'approval': '0'                        # use approval = 0 to mean no decision, -1 to be rejected, 1 to be approved
            }

            reduced_data, rdextra_value = run_custom_data_processor(dp, extras, rdextra_value) # uses spectroscopy_processor to make ReducedDatum objects

            reduced_datum_extra = ReducedDatumExtra(
                        target = target,
                        data_product = dp,
                        data_type = dp.data_product_type,
                        key = 'spec_extras'
                        value = rdextra_value
                    )
            reduced_datum_extra.save()
        return []


        # Work flow: run a chron to ingest new banzai reductions from the archive (how to filter for this??? perhaps just a time frame?). This script then makes a dataproduct and reduceddatum object for the spectrum, with approval = 0 as default. 
        # Target pages need to be updated to query reduceddatum table with approval flag '0' or '1'
        # Floyds Inbox needs to query for approval flag '0' with buttons that link to a script changing the reduceddatum approval flag to 1 or -1. Changing the flag to -1 should call the modify_sequence function in scheduling.py to re-request the sequence immediately (mimiccing markbad behaviour).
        # Run a one-time script to make all previous spectra approved, with flag approval = 1 in ReducedDatum table