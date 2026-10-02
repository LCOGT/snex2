import os
import requests
import logging
from astropy.time import Time
import json

from datetime import datetime, date
import numpy as np
from django.contrib.auth.models import User
from django.conf import settings
from custom_code.scheduling import save_comments
from custom_code.utils import measured, unsubtracted_q

from collections import OrderedDict
from guardian.shortcuts import get_objects_for_user
from tom_dataproducts.models import ReducedDatum

logger = logging.getLogger(__name__)


instrument_dict = {'2M0-FLOYDS-SCICAM': 'floyds',
                    '1M0-SCICAM-SINISTRO': 'sinistro',
                    '2M0-SCICAM-MUSCAT': 'muscat',
                    '0M4-SCICAM-SBIG': 'sbig0m4',
                    '0M4-SCICAM-QHY600': 'qhy',
                    }

priority_dict = {'NORMAL': 'normal',
                    'TIME_CRITICAL': 'time_critical',
                    'RAPID_RESPONSE': 'immediate_too'}

def save_observation_comment(observation, previous_state):
    logger.info('Observation change state hook: %s from %s to %s', observation, previous_state, observation.status)
    if previous_state == '':
        comment = observation.parameters.get('comment')
        obs_group = observation.observationgroup_set.first()
        if comment and obs_group:
            user = User.objects.filter(username=observation.parameters.get('start_user')).first()
            save_comments(comment, obs_group.id, user)

def _str_to_timestamp(datestring):
    """
    Converts string to a timestamp compatible with MYSQL timestamp field
    """
    timestamp = datetime.strptime(datestring, '%Y-%m-%dT%H:%M:%S')
    return timestamp.strftime('%Y-%m-%d %H:%M:%S')


def _str_to_jd(datestring):
    """
    Converts string to JD compatible with MYSQL double field
    """
    newdatestring = _str_to_timestamp(datestring)
    return np.round(Time(newdatestring, format='iso', scale='utc').jd, 8)


def _get_tns_params(target):
    logger.info(f'Target sent for TNS parameters, {target}')
    names = [target.name] + [t.name for t in target.aliases.all()]

    tns_name = False
    for name in names:
        if 'SN' in name[:3]:
            tns_name = name.replace(' ','').replace('SN', '')
            break
        elif 'AT' in name[:3]:
            tns_name = name.replace(' ','').replace('AT', '')
            break

    if not tns_name:
        return {'status': 'No TNS name'}

    api_key = os.environ['TNS_APIKEY']
    tns_id = os.environ['TNS_APIID']

    tns_url = 'https://www.wis-tns.org/api/get/object'
    json_list = [('objname',tns_name), ('objid',''), ('photometry','1'), ('spectra','0')]
    json_file = OrderedDict(json_list)

    try:
        logger.info(f'Querying TNS for target {target} to url {tns_url} and json file {json_file}')
        response = requests.post(tns_url, headers={'User-Agent': 'tns_marker{"tns_id":'+str(tns_id)+', "type":"bot", "name":"SNEx_Bot1"}'}, data={'api_key': api_key, 'data': json.dumps(json_file)})

        parsed = json.loads(response.text, object_pairs_hook=OrderedDict)
        result = json.dumps(parsed, indent=4)

        result = json.loads(result)
        discoverydate = result['data']['discoverydate']
        discoverymag = result['data']['discoverymag']
        discoveryfilt = result['data']['discmagfilter']['name']


        nondets = {}
        dets = {}

        photometry = result['data']['photometry']
        for phot in photometry:
            remarks = phot['remarks']
            if 'Last non detection' in remarks:
                nondet_jd = phot['jd']
                nondet_filt = phot['filters']['name']
                nondet_limmag = phot['limflux']

                nondets[nondet_jd] = [nondet_filt, nondet_limmag]

            else:
                det_jd = phot['jd']
                det_filt = phot['filters']['name']
                det_mag = phot['flux']

                dets[det_jd] = [det_filt, det_mag]


        first_det = min(dets.keys())

        last_nondet = 0
        for nondet, phot in nondets.items():
            if nondet > last_nondet and nondet < first_det:
                last_nondet = nondet

        response_data = {'success': 'Completed',
                         'nondetection': '{} ({})'.format(date.strftime(Time(last_nondet, scale='utc', format='jd').datetime, "%m/%d/%Y"), round(last_nondet, 2)) if last_nondet > 0 else None,
                         'nondet_mag': nondets[last_nondet][1] if last_nondet > 0 else None,
                         'nondet_filt': nondets[last_nondet][0] if last_nondet > 0 else None,
                         'detection': '{} ({})'.format(date.strftime(Time(first_det, scale='utc', format='jd').datetime, "%m/%d/%Y"), round(first_det, 2)),
                         'det_mag': dets[first_det][1],
                         'det_filt': dets[first_det][0]}
    
    except:
        logger.warning('TNS parameter ingestion failed for target {}'.format(target))
        response_data = {'failure': 'Parameters not ingested'}

    return response_data
        
def find_images(target, user, allimages=False):
    datums = ReducedDatum.objects.filter(target=target, data_type='photometry', value__has_key='basename').filter(unsubtracted_q())
    if not settings.TARGET_PERMISSIONS_ONLY:
        datums = get_objects_for_user(user, 'tom_dataproducts.view_reduceddatum', klass=datums)

    frames = OrderedDict()
    for rd in datums.order_by('-timestamp'):
        frames.setdefault(rd.value['basename'], rd)
        if not allimages and len(frames) == 8:
            break
    if not frames:
        logger.info(f'No images found for target {target}')
        return [], [], [], [], [], [], [], [], [], []

    def pixel(v):
        v = measured(v)
        return 9999 if v is None else round(v)

    def fwhm_label(v):
        v = measured(v)
        return '' if v is None else f'{v:.2f}"'

    def wcs_label(w):
        return '' if w is None else ('Good' if w == 0 else 'Failed')

    basenames, datums = list(frames), list(frames.values())
    return (basenames,
            [rd.timestamp.strftime('%m/%d/%Y') for rd in datums],
            [(rd.value.get('telescope') or '')[:3] for rd in datums],
            [rd.value.get('instrument') or b.split('-')[1] for b, rd in zip(basenames, datums)],
            [rd.value.get('filter', '') for rd in datums],
            ['' if rd.value.get('exptime') is None else f"{rd.value['exptime']:.2f}s" for rd in datums],
            [pixel(rd.value.get('psfx')) for rd in datums],
            [pixel(rd.value.get('psfy')) for rd in datums],
            [fwhm_label(rd.value.get('fwhm')) for rd in datums],
            [wcs_label(rd.value.get('wcs')) for rd in datums])
