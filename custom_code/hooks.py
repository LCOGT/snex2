import os
import requests
import logging
from astropy.time import Time
import json
from tom_targets.models import Target

from datetime import datetime, date
import numpy as np
from django.contrib.auth.models import User
from django.conf import settings
import urllib
from custom_code.scheduling import save_comments
from custom_code.utils import _return_session, _load_table, _get_session, measured, unsubtracted_q

from sqlalchemy import create_engine, pool, and_, or_, not_
from sqlalchemy.orm import sessionmaker, aliased
from sqlalchemy.ext.automap import automap_base
from contextlib import contextmanager
from collections import OrderedDict
from guardian.shortcuts import get_groups_with_perms, get_objects_for_user
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
        
def find_images(target, username, allimages=False):
    user = username if isinstance(username, User) else User.objects.get(username=username)
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
        return int(round(measured(v))) if measured(v) is not None else 9999

    def wcs_label(w):
        return '' if w is None else ('Good' if int(w) == 0 else 'Failed')

    basenames, datums = list(frames), list(frames.values())
    return (basenames,
            [rd.timestamp.strftime('%m/%d/%Y') for rd in datums],
            [str(rd.value.get('telescope', ''))[:3] for rd in datums],
            [rd.value.get('instrument') or b.split('-')[1] for b, rd in zip(basenames, datums)],
            [rd.value.get('filter', '') for rd in datums],
            [f"{float(rd.value['exptime']):.2f}s" if rd.value.get('exptime') not in (None, '') else '' for rd in datums],
            [pixel(rd.value.get('psfx')) for rd in datums],
            [pixel(rd.value.get('psfy')) for rd in datums],
            [f"{measured(rd.value.get('fwhm')):.2f}\"" if measured(rd.value.get('fwhm')) is not None else '' for rd in datums],
            [wcs_label(rd.value.get('wcs')) for rd in datums])

def get_unreduced_spectra(allspec=True):
    '''
    Hook to find unreduced spectra for FLOYDS inbox
    '''
    token = os.environ['LCO_APIKEY']

    response = requests.get('https://observe.lco.global/api/proposals?active=True&limit=50/',
                             headers={'Authorization': 'Token ' + token}).json()

    proposals = [prop['id'] for prop in response['results']]
    
    with _get_session(db_address=settings.SNEX1_DB_URL) as db_session:
        speclcoraw = _load_table('speclcoraw', db_address=settings.SNEX1_DB_URL)
        targetnames = _load_table('targetnames', db_address=settings.SNEX1_DB_URL)
        targets = _load_table('targets', db_address=settings.SNEX1_DB_URL)
        classifications = _load_table('classifications', db_address=settings.SNEX1_DB_URL)
        spec = _load_table('spec', db_address=settings.SNEX1_DB_URL)

        original_filenames = [s.original for s in db_session.query(spec).filter(and_(spec.original!='None', spec.original!=None))]

        unreduced_spectra = db_session.query(speclcoraw).join(
                targets, speclcoraw.targetid==targets.id
        ).join(
                targetnames, speclcoraw.targetid==targetnames.targetid
        ).join(
                classifications, targets.classificationid==classifications.id, isouter=True
        ).filter(
            and_(
                not_(speclcoraw.filename.in_(original_filenames)), 
                speclcoraw.propid.in_(proposals),
                speclcoraw.filename.contains('e00.fits'),
                or_(
                    classifications.name != 'Standard', 
                    classifications.name == None
                ), 
                or_(
                    and_(
                        speclcoraw.type != 'LAMPFLAT', 
                        speclcoraw.type != 'ARC'
                    ), 
                speclcoraw.type == None
            ), 
            not_(speclcoraw.filepath.contains('bad')), 
            not_(targetnames.name.contains('test_'))
            )
        )
        pipeline_ids = [s.targetid for s in unreduced_spectra]
        propids = [s.propid for s in unreduced_spectra]
        dateobs = [s.dateobs for s in unreduced_spectra]
        paths = [s.filepath for s in unreduced_spectra]
        filenames = [s.filename for s in unreduced_spectra]
        imgpaths = [os.path.join(s.filepath.replace(settings.FLOYDS_DIR, '/snex2/data/floyds'), s.filename.replace('.fits', '.png')) for s in unreduced_spectra]

    return pipeline_ids, propids, dateobs, paths, filenames, imgpaths


def get_standards_from_snex1(pipeline_id):
    
    with _get_session(db_address=settings.SNEX1_DB_URL) as db_session:
        
        photlco = _load_table('photlco', db_address=settings.SNEX1_DB_URL)
        #targetnames = _load_table('targetnames', db_address=settings.SNEX1_DB_URL)
        targets = _load_table('targets', db_address=settings.SNEX1_DB_URL)

        std = aliased(photlco)
        obj = aliased(photlco)

        standard_info = db_session.query(
            std.objname, std.filename, std.filter, std.dateobs,
            std.telescope, std.instrument
        ).distinct().join(
            targets, std.targetid==targets.id 
        ).filter(
            and_(
                obj.telescopeid==std.telescopeid,
                obj.instrumentid==std.instrumentid,
                targets.classificationid==1,
                obj.filter==std.filter,
                obj.dayobs==std.dayobs,
                obj.quality==127,
                std.quality==127,
                obj.targetid==pipeline_id
            )
        )

    return [dict(r._mapping) for r in standard_info]

def download_test_image_from_archive():
    """
    Download a test image from the LCO archive to test image thumbnails.
    NOTE: Only runs in dev
    Creates any directories needed to store the image and thumbnail.
    Checks if the image exists and if not, downloads it from the archive.
    Returns the image parameters needed to display its thumbnail.
    """
    ### Check if thumbnail directory exists, and if not make it
    thumbnail_directory = settings.FITS_DIR
    if not os.path.isdir(thumbnail_directory):
        os.makedirs(os.path.join(settings.BASE_DIR, thumbnail_directory))

    if not os.path.isdir(settings.THUMB_DIR):
        os.mkdir(os.path.join(settings.BASE_DIR, settings.THUMB_DIR))

    ### Check if test image already exists in thumbnail directory,
    ### and if not download it
    # 4 test images, first 3 are public, last is of 23ixf
    test_thumbnail_basenames = ["elp1m008-fa16-20250725-0103-e91","elp0m414-sq31-20250713-0229-e00","ogg0m455-sq30-20250712-0249-e91","tfn0m436-sq33-20250718-0265-e91"]
    for test_thumbnail_basename in test_thumbnail_basenames:
        if not any([test_thumbnail_basename in f for f in os.listdir(thumbnail_directory)]):
            ### GET it from the archive
            token = settings.FACILITIES['LCO']['api_key']
            url = settings.FACILITIES['LCO']['archive_url']

            results = requests.get(url, 
                                headers={'Authorization': f'Token {token}'}, 
                                params={'basename': test_thumbnail_basename}).json()["results"]
            thumbnail_url = results[0]["url"]
            thumbnail_filename = results[0]["filename"]
            # Download image and funpack it
            urllib.request.urlretrieve(thumbnail_url, os.path.join(settings.BASE_DIR, thumbnail_directory, thumbnail_filename))
            os.system('funpack -D '+ thumbnail_directory + thumbnail_filename)

    filepaths = ['','','','']
    filenames = test_thumbnail_basenames
    dates = ["2025-07-25","2025-07-13","2025-07-12","2025-07-11"]
    teles = ["1m","0m4","0m4","0m4"]
    instr = ["kb78","kb78","kb78","kb78"]
    filters = ["B","r","g","V"]
    exptimes = ["300s","180s","120s","90s"]
    psfxs = [9999,9999,9999,9999]
    psfys = [9999,9999,9999,9999]
    
    return (
        filepaths, 
        filenames, 
        dates, 
        teles, 
        instr,
        filters, 
        exptimes, 
        psfxs, 
        psfys,
    )
