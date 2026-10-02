#!/usr/bin/env python

"""
Convert FITS data in to a standard web displayable format (e.g. GIF).
Modified from SNEx
"""
import glob
import os
import numpy as np
from astropy.io import fits
from PIL import Image, ImageDraw
from django.conf import settings
import tempfile
import logging
from custom_code.utils import download_archive_frame

logger = logging.getLogger(__name__)


# ************************************************************
def getsky(data):
    """
    Determine the sky parameters for a FITS data extension.

    data -- array holding the image data
    """

    # maximum number of interations for mean,std loop
    maxiter = 30

    # maximum number of data points to sample
    maxsample = 10000

    # size of the array
    ny, nx = data.shape

    # how many sampels should we take?
    if data.size > maxsample:
        nsample = maxsample
    else:
        nsample = data.size

    # create sample indicies
    xs = np.random.uniform(low=0, high=nx, size=nsample).astype('L')
    ys = np.random.uniform(low=0, high=ny, size=nsample).astype('L')

    # sample the data
    sample = data[ys, xs].copy()
    sample = sample.reshape(nsample)

    # determine the clipped mean and standard deviation
    mean = sample.mean()
    std = sample.std()
    oldsize = 0
    niter = 0
    while oldsize != sample.size and niter < maxiter:
        niter += 1
        oldsize = sample.size
        wok = (sample < mean + 3 * std)
        sample = sample[wok]
        wok = (sample > mean - 3 * std)
        sample = sample[wok]
        mean = sample.mean()
        std = sample.std()

    return mean, std


# ************************************************************
def make_depth_256(data, sky=None, sig=None, depth=256, zerosig=-1, spansig=6):
    """
    Convert image to 256 colors.

    data is an image array

    Scale image data so that black is (sky - sig) and white is (sky + 5
    * sig). If optional sky and sig keywords are not set, they are
    calculated using getsky(data)

   """
    data = data.astype(np.float64)
    if sky is None or sig is None:
        # get the scaling parameters
        sky2, sig2 = getsky(data)
        if sky is None:
            sky = sky2
        if sig is None:
            sig = sig2

    # set the color range
    zero = sky + zerosig * sig
    span = spansig * sig

    # scale the data to the requested display values
    # greys
    data -= zero
    data *= (depth - 1) / span

    # black
    w = data < 0
    data[w] = 0

    # white
    w = data > (depth - 1)
    data[w] = (depth - 1)

    data += 256 - depth

    return data


FRAME_CACHE_SIZE = 50


def cached_frame(basename):
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
    frames = sorted(glob.glob(os.path.join(cache_dir, '*.fits*')), key=os.path.getmtime, reverse=True)
    for old in frames[FRAME_CACHE_SIZE:]:
        os.remove(old)
    return path


def frame_cutout(path, region):
    with fits.open(path) as hdulist:
        hdu = hdulist['SCI'] if 'SCI' in hdulist else hdulist[1 if path.endswith('.fz') else 0]
        ny, nx = hdu.shape
        x1, x2, y1, y2 = region
        x1, y1, x2, y2 = max(x1, 0), max(y1, 0), min(x2, nx - 1), min(y2, ny - 1)
        pixels = hdu.section if hasattr(hdu, 'section') else hdu.data
        return np.array(pixels[y1:y2 + 1, x1:x2 + 1], dtype=np.float64)


# ***************************************************************************
def make_thumb(basenames, grow=1.0, sky=None, sig=None, x=900, y=900, width=250, height=250, ticks=False, spansig=4, skip=0, fixscale=None):
    """
    Make thumbnails from a FITS image downloaded from LCO archive
    """
    region = [round(x-(width/grow)), round(x+(width/grow)), round(y-(height/grow)), round(y+(height/grow))]
    # make the thumbnails
    outfiles = []
    for basename in basenames:
        data = frame_cutout(cached_frame(basename), region)
        data = make_depth_256(data, sky=sky, sig=sig, zerosig=0, spansig=spansig)

        im = Image.fromarray(data.astype(np.uint8), mode='L')
        nx, ny = im.size
        im = im.resize((int(nx * grow), int(ny * grow)), Image.LANCZOS if grow < 1.0 else Image.NEAREST)
        im = im.transpose(Image.FLIP_TOP_BOTTOM).convert('RGB')

        ### Add crosshair
        if ticks:
            x1, x2, y1, y2 = region
            xoff = -0.5
            yoff = 1.0

            x_new = int(round((x + xoff - max([0, x1])) * grow))
            y_new = int(round((min([y2, 4096]) - y + yoff) * grow))

            draw = ImageDraw.Draw(im)
            draw.line((x_new,y_new+7,x_new,y_new+25), fill='white')
            draw.line((x_new-7,y_new,x_new-25,y_new), fill='white')

        # make the thumbs
        if grow == 1.0 and not sig:
            newfile = basename + '.webp'
        else:
            newfile = basename + 'grow{}sig{}.webp'.format(grow, sig)
        outfile = os.path.join(settings.THUMB_DIR,newfile)
        logger.info(f'out file {outfile}')
        with open(outfile, 'wb') as f:
            im.save(f, 'WEBP')

        outfiles.append(newfile)

    return outfiles
