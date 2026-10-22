import datetime
import zipfile

import requests
from dateutil.parser import parse
from django.conf import settings

from custom_code.utils import frame_night

STANDARDS_PROPOSAL = 'FLOYDS standards'
NIGHT_WINDOW = datetime.timedelta(hours=16)


def is_floyds_frame(product_id):
    return bool(frame_night(product_id)) and product_id.split('-')[1].startswith('en') and product_id.endswith('-e00')


def _archive(params):
    lco = settings.FACILITIES['LCO']
    response = requests.get(lco['archive_url'], params={**params, 'include_related_frames': False},
                            headers={'Authorization': 'Token ' + lco['api_key']}, timeout=(10, 120))
    response.raise_for_status()
    return response.json()['results']


def _night_frames(frame, nights):
    key = (frame['SITEID'], frame['INSTRUME'], frame['basename'].split('-')[2])
    if key not in nights:
        observed = parse(frame['DATE_OBS'])
        frames = _archive({'SITEID': key[0], 'INSTRUME': key[1], 'RLEVEL': 0, 'limit': 1000,
                           'start': (observed - NIGHT_WINDOW).strftime('%Y-%m-%d %H:%M'),
                           'end': (observed + NIGHT_WINDOW).strftime('%Y-%m-%d %H:%M')})
        nights[key] = [f for f in frames if f['basename'].split('-')[2] == key[2]]
    return nights[key]


def raw_set(product_id, include_standard, nights):
    found = _archive({'basename_exact': product_id})
    if not found:
        return [], []
    frame = found[0]
    night = _night_frames(frame, nights)
    science = sorted((f for f in night if f['observation_id'] == frame['observation_id']), key=lambda f: f['basename'])
    if not include_standard or frame.get('PROPID') == STANDARDS_PROPOSAL:
        return science, []
    observed = parse(frame['DATE_OBS'])
    blocks = {}
    for f in night:
        if f.get('PROPID') == STANDARDS_PROPOSAL:
            blocks.setdefault(f['observation_id'], []).append(f)
    offsets = [(abs(parse(f['DATE_OBS']) - observed), block) for block, frames in blocks.items()
               for f in frames if f.get('OBSTYPE') == 'SPECTRUM']
    standard = sorted(blocks[min(offsets)[1]], key=lambda f: f['basename']) if offsets else []
    return science, standard


class _Buffer:

    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data += data
        return len(data)

    def flush(self):
        pass

    def take(self):
        data = bytes(self.data)
        self.data.clear()
        return data


def stream_raw_zip(entries, include_standard):
    buffer, nights = _Buffer(), {}
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_STORED, allowZip64=True) as archive:
        for folder, product_id in entries:
            try:
                science, standard = raw_set(product_id, include_standard, nights)
            except requests.RequestException as e:
                archive.writestr(folder + 'MISSING.txt', f'Could not look up {product_id} in the LCO archive: {e}\n')
                yield buffer.take()
                continue
            if not science:
                archive.writestr(folder + 'MISSING.txt', f'{product_id} was not found in the LCO archive.\n')
            elif include_standard and not standard and science[0].get('PROPID') != STANDARDS_PROPOSAL:
                archive.writestr(folder + 'NO_STANDARD.txt', f'No FLOYDS standard was found for the night of {product_id}.\n')
            for frame in science + standard:
                try:
                    response = requests.get(frame['url'], stream=True, timeout=(10, 300))
                    response.raise_for_status()
                except requests.RequestException as e:
                    archive.writestr(folder + frame['filename'] + '.MISSING.txt', f'Could not download {frame["filename"]}: {e}\n')
                    yield buffer.take()
                    continue
                with response, archive.open(folder + frame['filename'], 'w', force_zip64=True) as destination:
                    for chunk in response.iter_content(1 << 20):
                        destination.write(chunk)
                        yield buffer.take()
                yield buffer.take()
    yield buffer.take()
