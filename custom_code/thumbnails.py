#!/usr/bin/env python

import glob
import os
import re
import requests
from django.conf import settings
import tempfile
import time
import logging
from custom_code.utils import download_archive_frame

logger = logging.getLogger(__name__)

FRAME_CACHE_SIZE = 50
CACHE_SECONDS = 24 * 60 * 60


def _evict(pattern, keep=None):
    ages = {}
    for path in glob.glob(pattern):
        try:
            ages[path] = time.time() - os.path.getmtime(path)
        except FileNotFoundError:
            pass
    for position, path in enumerate(sorted(ages, key=ages.get)):
        if ages[path] > CACHE_SECONDS or (keep is not None and position >= keep):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def cached_frame(basename):
    if not re.fullmatch(r'[\w-]+', basename):
        raise FileNotFoundError(f'{basename} is not a frame name')
    cache_dir = os.path.join(settings.THUMB_DIR, 'frames')
    os.makedirs(cache_dir, exist_ok=True)
    cached = glob.glob(os.path.join(cache_dir, basename + '.fits*'))
    if cached:
        os.utime(cached[0])
        return cached[0]
    frame = download_archive_frame(basename)
    if frame is None:
        raise FileNotFoundError(f'{basename} is not in the LCO archive')
    archive_name, content = frame
    path = os.path.join(cache_dir, basename + ('.fits.fz' if archive_name.endswith('.fz') else '.fits'))
    with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False) as f:
        f.write(content)
    os.replace(f.name, path)
    _evict(os.path.join(cache_dir, '*.fits*'), FRAME_CACHE_SIZE)
    return path


def cutout_url(basename, psfx, psfy, zoom=1.0, sigma=4.0):
    params = {'width': 500, 'height': 500, 'zoom': zoom, 'sigma': sigma}
    if psfx < 9999 and psfy < 9999:
        params.update({'x': psfx, 'y': psfy, 'ticks': 'true'})
    else:
        params.update({'x': 1024, 'y': 1024})
    response = requests.get(settings.FACILITIES['LCO']['cutout_url'] + basename + '/',
                            headers={'Authorization': f"Token {settings.FACILITIES['LCO']['api_key']}"},
                            params=params, timeout=(30, 120))
    response.raise_for_status()
    return response.json()['url']
