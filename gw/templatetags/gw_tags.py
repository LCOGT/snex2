from django.contrib.auth.models import Group
from django import template
from plotly import offline
from plotly import graph_objs as go
from astropy.io import fits
from astropy.visualization import ZScaleInterval
from astropy.wcs import WCS
from astropy.wcs.utils import skycoord_to_pixel
from astropy.coordinates import SkyCoord
import numpy as np
import logging

from tom_targets.models import Target
from tom_common.hooks import run_hook

logger = logging.getLogger(__name__)

register = template.Library()

@register.filter
def has_gw_permissions(user):
    try:
        gw_group = Group.objects.get(name='GWO4')
    except Group.DoesNotExist:
        # Added to get a new SNEX installation to load
        return False

    if user in gw_group.user_set.all():
        return True
    return False


@register.inclusion_tag('gw/partials/galaxy_aladin_skymap.html')
def galaxy_distribution(galaxies):
    galaxy_list = []

    for galaxy in galaxies:
        galaxy_list.append(
            {'name': galaxy.catalog_objname, 
             'ra': galaxy.ra, 
             'dec': galaxy.dec,
             'score': galaxy.score}
        )

    context = {'targets': galaxy_list}
    return context


@register.inclusion_tag('gw/plot_galaxy_image.html')
def plot_galaxy_image(image, galaxy):
    HALF_SIZE = 0.9/60

    half_width = HALF_SIZE / np.cos(np.radians(galaxy.dec))
    corners = SkyCoord([galaxy.ra + half_width, galaxy.ra - half_width, galaxy.ra + half_width, galaxy.ra - half_width],
                       [galaxy.dec - HALF_SIZE, galaxy.dec - HALF_SIZE, galaxy.dec + HALF_SIZE, galaxy.dec + HALF_SIZE], unit='deg')

    fig = go.Figure().set_subplots(1, len(image['filenames']))
    for column, filename in enumerate(image['filenames'], start=1):
        with fits.open(filename) as hdulist:
            hdu = hdulist['SCI'] if 'SCI' in hdulist else hdulist[1 if filename.endswith('.fz') else 0]
            img = hdu.data
            wcs = WCS(hdu.header)
        x, y = skycoord_to_pixel(corners, wcs)
        x_min, x_max = max(int(np.min(x)), 0), max(int(np.max(x)), 0)
        y_min, y_max = max(int(np.min(y)), 0), max(int(np.max(y)), 0)
        cutout = img[y_min:y_max, x_min:x_max]
        if cutout.size:
            zmin, zmax = [int(el) for el in ZScaleInterval().get_limits(cutout)]
            fig.add_trace(go.Heatmap(
                x=np.linspace(galaxy.ra + half_width, galaxy.ra - half_width, cutout.shape[1]),
                y=np.linspace(galaxy.dec - HALF_SIZE, galaxy.dec + HALF_SIZE, cutout.shape[0]),
                z=cutout, zmin=zmin, zmax=zmax, showscale=False), row=1, col=column)
    fig.update_xaxes(matches='x', autorange='reversed')
    fig.update_yaxes(matches='y')
    fig.update_layout(autosize=False, width=300 * len(image['filenames']), height=300, margin=dict(l=0, r=0, b=0, t=0))

    return {'plot': offline.plot(fig, output_type='div', show_link=False)}


@register.inclusion_tag('gw/partials/nonlocalizedevent_info.html')
def event_info(sequence):

    return {'sequence': sequence, 'localization': sequence.localization}


def get_target_from_galaxy(galaxy):
    target = Target.objects.filter(gwfollowupgalaxy_id=galaxy.id)
    if not target:
        targ_query = Target.objects.filter(name=galaxy.catalog_objname)
        if not targ_query:
            return False
        return targ_query.first()
    return target.first()   


@register.filter
def has_images(galaxy,username):
    targ = get_target_from_galaxy(galaxy)
    if not targ:
        return False
    try:
        filenames, dates, teles, instr, filters, exptimes, psfxs, psfys, fwhms, wcs = run_hook('find_images', targ, username)
    except:
        return False

    if filenames:
        return True

    return False


@register.filter
def get_target_id(galaxy):
    targ = get_target_from_galaxy(galaxy)
    if not targ:
        return None
    return targ.id
