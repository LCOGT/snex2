import os
import requests
import logging
from astropy.time import Time
import json
from tom_targets.models import Target
from custom_code.management.commands.ingest_ztf_data import get_ztf_data

from datetime import datetime, date
import numpy as np
from django.contrib.auth.models import User
from django.conf import settings
import urllib
from custom_code.scheduling import save_comments

from sqlalchemy import create_engine, pool, and_, or_, not_, text
from sqlalchemy.orm import sessionmaker, aliased
from sqlalchemy.ext.automap import automap_base
from contextlib import contextmanager
from collections import OrderedDict

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

@contextmanager
def _get_session(db_address):
    Base = automap_base()
    engine = create_engine(db_address, poolclass=pool.NullPool)
    Base.metadata.bind = engine

    db_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = db_session()

    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()

def save_observation_comment(observation, previous_state):
    logger.info('Observation change state hook: %s from %s to %s', observation, previous_state, observation.status)
    if previous_state == '':
        comment = observation.parameters.get('comment')
        obs_group = observation.observationgroup_set.first()
        if comment and obs_group:
            user = User.objects.filter(username=observation.parameters.get('start_user')).first()
            save_comments(comment, obs_group.id, user)

def _return_session(db_address=settings.SNEX1_DB_URL):
    ### This one is not run within a with loop, must be closed manually
    Base = automap_base()
    engine = create_engine(db_address, poolclass=pool.NullPool)
    Base.metadata.bind = engine

    db_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = db_session()

    return session


def _load_table(tablename, db_address):
    Base = automap_base()
    engine = create_engine(db_address, poolclass=pool.NullPool)
    Base.prepare(engine, reflect=True)

    table = getattr(Base.classes, tablename)
    return(table)
 

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


def target_post_save(target, created, group_names=None, wrapped_session=None):
 
    logger.info('Target post save hook: %s created: %s', target, created)
    
    if not created:
        
        ### Add the last nondetection and first detection from TNS, if it exists
        tns_results = _get_tns_params(target)
        if tns_results.get('success', ''):
            if tns_results['nondetection'] == None:
                print('No TNS last nondetection found for target',target)
            else:
                nondet_date = tns_results['nondetection'].split()[0]
                nondet_jd = tns_results['nondetection'].split()[1].replace('(', '').replace(')', '')
                nondet_value = json.dumps({
                    'date': nondet_date,
                    'jd': nondet_jd,
                    'mag': tns_results['nondet_mag'],
                    'filt': tns_results['nondet_filt'],
                    'source': 'TNS'
                })

                logger.info(f'Saving target {target} after TNS nondetection ingestion')
                Target.objects.filter(pk=target.pk).update(last_nondetection=nondet_value)
            if tns_results['detection'] == None:
                print('No TNS detection found for target',target)
            else:
                det_date = tns_results['detection'].split()[0]
                det_jd = tns_results['detection'].split()[1].replace('(', '').replace(')', '')
                det_value = json.dumps({
                    'date': det_date,
                    'jd': det_jd,
                    'mag': tns_results['det_mag'],
                    'filt': tns_results['det_filt'],
                    'source': 'TNS'
                })
                
                logger.info(f'Target {target} first detection saved from TNS.')
                Target.objects.filter(pk=target.pk).update(first_detection=det_value)

        ### Ingest ZTF data, if a ZTF target
        # get_ztf_data(target) #want to test first with new url before implementing

    else:

        if wrapped_session:
            db_session = wrapped_session
    
        else:
            db_session = _return_session(settings.SNEX1_DB_URL)
    
        Targets = _load_table('targets', db_address=settings.SNEX1_DB_URL)
        Targetnames = _load_table('targetnames', db_address=settings.SNEX1_DB_URL)
        Groups = _load_table('groups', db_address=settings.SNEX1_DB_URL)
        # Insert into SNEx 1 db
        if group_names:
            groupidcode = 0
            for group_name in group_names:
                groupidcode += int(db_session.query(Groups).filter(Groups.name==group_name).first().idcode)
        else:
            groupidcode = 32769 #Default in SNEx1
        snex1_target = Targets(id=target.id, ra0=target.ra, dec0=target.dec, groupidcode=groupidcode, lastmodified=target.modified, datecreated=target.created)
        db_session.add(snex1_target)
        db_session.add(Targetnames(targetid=target.id, name=target.name, datecreated=target.created, lastmodified=target.modified))
    
        if not wrapped_session:
            try:
                db_session.commit()
            except:
                db_session.rollback()
            finally:
                db_session.close()
        
        else:
            db_session.flush()

def find_images_from_snex1(targetid, username, allimages=False):
    '''
    Hook to find filenames of images in SNEx1,
    given a target ID
    '''
    
    with _get_session(db_address=settings.SNEX1_DB_URL) as db_session:
        # now queries the snex1 database directly as .execute instead of .query, so don't need to load in Photlco as a table
        # Photlco = _load_table('photlco', db_address=settings.SNEX1_DB_URL)
        Users = _load_table('users', db_address=settings.SNEX1_DB_URL)
        Targets = _load_table('targets', db_address=settings.SNEX1_DB_URL)
        
        this_user = db_session.query(Users).filter(Users.name==username).first()
        this_target = db_session.query(Targets).filter(Targets.id==targetid).first()

        if not allimages:
            query = db_session.execute(
                            text("SELECT * FROM photlco WHERE targetid = :tid AND filetype = 1 " \
                            "AND BIT_COUNT(COALESCE(groupidcode, :target_perm) & :user_groupid) > 0 ORDER BY id DESC LIMIT 8"),
                            {'tid':targetid, 'target_perm': this_target.groupidcode, 'user_groupid': this_user.groupidcode}).all()
        else:
            query = db_session.execute(
                            text("SELECT * FROM photlco WHERE targetid = :tid AND filetype = 1 " \
                            "AND BIT_COUNT(COALESCE(groupidcode, :target_perm) & :user_groupid) > 0 ORDER BY id DESC"),
                            {'tid':targetid, 'target_perm': this_target.groupidcode, 'user_groupid': this_user.groupidcode}).all()
        
        filepaths = [q.filepath.replace(settings.LSC_DIR, '').replace('/supernova/data/', '') for q in query]
        if len(filepaths)==0:
            logger.info(f'No images found for target {targetid}')
            return [], [], [], [], [], [], [], [], []
        filenames = [q.filename.replace('.fits', '') for q in query]
        dates = [date.strftime(q.dateobs, '%m/%d/%Y') for q in query]
        teles = [q.telescope[:3] for q in query]
        instr = [q.instrument for q in query]
        filters = [q.filter for q in query]
        exptimes = [str(round(float(q.exptime))) + 's' for q in query]
        psfxs = [int(round(q.psfx)) for q in query]
        psfys = [int(round(q.psfy)) for q in query]

    logger.info('Found file names for target {}'.format(targetid))

    return filepaths, filenames, dates, teles, instr, filters, exptimes, psfxs, psfys

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
        targetids = [s.targetid for s in unreduced_spectra]
        propids = [s.propid for s in unreduced_spectra]
        dateobs = [s.dateobs for s in unreduced_spectra]
        paths = [s.filepath for s in unreduced_spectra]
        filenames = [s.filename for s in unreduced_spectra]
        imgpaths = [os.path.join(s.filepath.replace(settings.FLOYDS_DIR, '/snex2/data/floyds'), s.filename.replace('.fits', '.png')) for s in unreduced_spectra]

    return targetids, propids, dateobs, paths, filenames, imgpaths


def get_standards_from_snex1(target_id):
    
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
                obj.targetid==target_id
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
