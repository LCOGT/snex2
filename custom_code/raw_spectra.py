import datetime
import os
import tempfile
import zipfile

import requests
from dateutil.parser import parse
from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.text import slugify

from custom_code.utils import frame_night

STANDARDS_PROPOSAL = 'FLOYDS standards'
NIGHT_WINDOW = datetime.timedelta(hours=16)
REDUCTION_FILES_LIMIT = 300 * 1024 * 1024
ARCHIVE_SUFFIXES = ('.zip', '.tar', '.tar.gz', '.tgz')


def save_reduction_files(target, files):
    folder = f'reduction_files/{slugify(target.name)}/{timezone.now():%Y%m%dT%H%M%S}'
    if len(files) == 1 and files[0].name.lower().endswith(ARCHIVE_SUFFIXES):
        path = default_storage.save(f'{folder}/{os.path.basename(files[0].name)}', files[0])
    else:
        with tempfile.TemporaryFile() as bundle:
            with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_STORED, allowZip64=True) as archive:
                for upload in files:
                    with archive.open(os.path.basename(upload.name), 'w', force_zip64=True) as destination:
                        for chunk in upload.chunks():
                            destination.write(chunk)
            bundle.seek(0)
            path = default_storage.save(f'{folder}/reduction_files.zip', File(bundle, name='reduction_files.zip'))
    return {'path': path, 'name': os.path.basename(path)}


def reduction_files(spectrum):
    bundle = (spectrum.value or {}).get('reduction_files') if spectrum else None
    return bundle if bundle and default_storage.exists(bundle['path']) else None


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


def _copy(source, destination, buffer):
    for chunk in source:
        destination.write(chunk)
        yield buffer.take()


def _archive_frames(archive, buffer, folder, product_id, include_standard, nights):
    try:
        science, standard = raw_set(product_id, include_standard, nights)
    except requests.RequestException as e:
        archive.writestr(folder + 'MISSING.txt', f'Could not look up {product_id} in the LCO archive: {e}\n')
        return
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
            continue
        with response, archive.open(folder + frame['filename'], 'w', force_zip64=True) as destination:
            yield from _copy(response.iter_content(1 << 20), destination, buffer)


def stream_zip(items, include_standard=False):
    buffer, nights = _Buffer(), {}
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_STORED, allowZip64=True) as archive:
        for kind, name, source in items:
            if kind == 'text':
                archive.writestr(name, source)
            elif kind == 'file':
                with default_storage.open(source, 'rb') as stored, archive.open(name, 'w', force_zip64=True) as destination:
                    yield from _copy(iter(lambda: stored.read(1 << 20), b''), destination, buffer)
            elif kind == 'archive':
                yield from _archive_frames(archive, buffer, name, source, include_standard, nights)
            yield buffer.take()
    yield buffer.take()
