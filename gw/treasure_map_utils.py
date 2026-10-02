import json
import logging
import os
import requests

logger = logging.getLogger(__name__)

TM_TOKEN = os.getenv('TM_TOKEN', '')
TM_API_URL = 'https://treasuremap.space/api/v1'
INSTRUMENT_NAMES = {'0M4-SCICAM-QHY600': 'QHY',
                    '1M0-SCICAM-SINISTRO': 'Sinistro',
                    '2M0-SCICAM-MUSCAT': 'MuSCAT',
                    '2M0-SCICAM-SPECTRAL': 'Spectral'}
BAND_DICT = {'U': 'U', 'B': 'B', 'V': 'V', 'R': 'R', 'I': 'I',
             'up': 'u', 'gp': 'g', 'rp': 'r', 'ip': 'i', 'zs': 'z', 'w': 'other'}


def tm_request(method, path, **kwargs):
    response = requests.request(method, TM_API_URL + path, headers={'api_token': TM_TOKEN}, **kwargs)
    response.raise_for_status()
    return response.json()


def get_tm_instrument_id(instrument_type):
    instruments = tm_request('GET', '/instruments', params={'name': INSTRUMENT_NAMES[instrument_type]})
    if len(instruments) != 1:
        names = [instrument['instrument_name'] for instrument in instruments]
        raise ValueError(f'Expected one Treasure Map instrument for {instrument_type}, found {names}')
    return instruments[0]['id']


def build_tm_pointings(target, observation_parameters):

    pointings = []

    planned_pointing = {'ra': float(target.ra),
                        'dec': float(target.dec),
                        'instrumentid': get_tm_instrument_id(observation_parameters['instrument_type']),
                        'time': observation_parameters['start'],
                        'status': 'planned',
                        'depth': 20.0,
                        'depth_unit': 'ab_mag',
                        'pos_angle': 0.0
    }

    for filt, band in BAND_DICT.items():
        if filt in observation_parameters.keys():
            copy_planned_pointing = dict(planned_pointing)
            copy_planned_pointing['band'] = band
            pointings.append(copy_planned_pointing)

    return pointings


def submit_tm_pointings(sequence, pointings):

    tm_planned_report = {'graceid': sequence.nonlocalizedevent.event_id,
                         'pointings': pointings
    }

    try:
        result = tm_request('POST', '/pointings', json=tm_planned_report)
    except requests.RequestException as e:
        logger.error(f'Submitting pointings to Treasure Map failed: {e}')
        return False

    if result.get('ERRORS'):
        logger.error(f'Treasure Map rejected pointings: {result["ERRORS"]}')
    if result.get('WARNINGS'):
        logger.warning(f'Treasure Map warnings: {result["WARNINGS"]}')

    return not result.get('ERRORS')


def cancel_tm_pointings(sequence, instrument_type):

    return tm_request('POST', '/cancel_all', json={'graceid': sequence.nonlocalizedevent.event_id,
                                                   'instrumentid': get_tm_instrument_id(instrument_type)})


def query_tm_pointings(sequence, status, wl_low=1000, wl_high=20000, wl_unit='angstrom'):

    params = {'graceid': sequence.nonlocalizedevent.event_id,
              'status': status,
              'wavelength_regime': json.dumps([wl_low, wl_high]),
              'wavelength_unit': wl_unit
    }

    return tm_request('GET', '/pointings', params=params)
