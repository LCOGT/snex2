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
                science, standard, is_standard = [], [], False
                try:
                    found = _archive({'basename_exact': source})
                    if found:
                        frame, observed = found[0], parse(found[0]['DATE_OBS'])
                        key = (frame['SITEID'], frame['INSTRUME'], frame['basename'].split('-')[2])
                        if key not in nights:
                            frames = _archive({'SITEID': key[0], 'INSTRUME': key[1], 'RLEVEL': 0, 'limit': 1000,
                                               'start': (observed - NIGHT_WINDOW).strftime('%Y-%m-%d %H:%M'),
                                               'end': (observed + NIGHT_WINDOW).strftime('%Y-%m-%d %H:%M')})
                            nights[key] = [f for f in frames if f['basename'].split('-')[2] == key[2]]
                        science = sorted((f for f in nights[key] if f['observation_id'] == frame['observation_id']),
                                         key=lambda f: f['basename'])
                        is_standard = frame.get('PROPID') == STANDARDS_PROPOSAL
                        offsets = [(abs(parse(f['DATE_OBS']) - observed), f['observation_id']) for f in nights[key]
                                   if f.get('PROPID') == STANDARDS_PROPOSAL and f.get('OBSTYPE') == 'SPECTRUM'
                                   ] if include_standard and not is_standard else []
                        if offsets:
                            standard = sorted((f for f in nights[key] if f['observation_id'] == min(offsets)[1]),
                                              key=lambda f: f['basename'])
                except requests.RequestException as e:
                    archive.writestr(name + 'MISSING.txt', f'Could not look up {source} in the LCO archive: {e}\n')
                    continue
                if not science:
                    archive.writestr(name + 'MISSING.txt', f'{source} was not found in the LCO archive.\n')
                elif include_standard and not standard and not is_standard:
                    archive.writestr(name + 'NO_STANDARD.txt', f'No FLOYDS standard was found for the night of {source}.\n')
                for frame in science + standard:
                    try:
                        response = requests.get(frame['url'], stream=True, timeout=(10, 300))
                        response.raise_for_status()
                    except requests.RequestException as e:
                        archive.writestr(name + frame['filename'] + '.MISSING.txt', f'Could not download {frame["filename"]}: {e}\n')
                        continue
                    with response, archive.open(name + frame['filename'], 'w', force_zip64=True) as destination:
                        yield from _copy(response.iter_content(1 << 20), destination, buffer)
            yield buffer.take()
    yield buffer.take()
