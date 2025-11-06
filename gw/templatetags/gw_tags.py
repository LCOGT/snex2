from django.contrib.auth.models import User, Group
from django import template
from plotly import offline
from plotly import graph_objs as go
from astropy.io import fits
from astropy.visualization import ZScaleInterval
from astropy.wcs import WCS
from astropy.wcs.utils import pixel_to_skycoord, skycoord_to_pixel
from astropy.coordinates import SkyCoord
import numpy as np
import sep
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


@register.inclusion_tag('gw/plot_triplets.html')#, takes_context=True)
def plot_triplets(triplet, galaxy, display_type):

    #This can be galaxy sizes times some factor.
    HALF_SIZE = 0.9/60 #deg

    plot_context = {}

    fig = go.Figure().set_subplots(1,3)
    
    for i, filetype in enumerate(['original', 'template', 'diff']):
        img_file = triplet[filetype]['filename']
        if img_file.endswith('fz'):
            ext = 1
        else:
            ext = 0
        hdu = fits.open(img_file)
        img = hdu[ext].data
        wcs = WCS(hdu[ext].header)
        hdu.close()

        if display_type == 'list':
            ###TODO: Change this:
            #galaxy_coord = SkyCoord(228.691875, 31.223633, unit='deg')#galaxy.ra, galaxy.dec, unit='deg')
            #galaxy_pix_ra, galaxy_pix_dec = skycoord_to_pixel(galaxy_coord, wcs)
            bottom_edge = SkyCoord(galaxy.ra, galaxy.dec-HALF_SIZE, unit='deg')
            top_edge = SkyCoord(galaxy.ra, galaxy.dec+HALF_SIZE, unit='deg')
            top_left = SkyCoord(galaxy.ra+HALF_SIZE/np.cos(top_edge.dec.rad), top_edge.dec.deg, unit='deg')
            top_right = SkyCoord(galaxy.ra-HALF_SIZE/np.cos(top_edge.dec.rad), top_edge.dec.deg, unit='deg')
            bot_left = SkyCoord(galaxy.ra+HALF_SIZE/np.cos(bottom_edge.dec.rad), bottom_edge.dec.deg, unit='deg')
            bot_right = SkyCoord(galaxy.ra-HALF_SIZE/np.cos(bottom_edge.dec.rad), bottom_edge.dec.deg, unit='deg')

            pix_top_left_ra, pix_top_left_dec = skycoord_to_pixel(top_left, wcs)
            pix_top_right_ra, pix_top_right_dec = skycoord_to_pixel(top_right, wcs)
            pix_bot_left_ra, pix_bot_left_dec = skycoord_to_pixel(bot_left, wcs)
            pix_bot_right_ra, pix_bot_right_dec = skycoord_to_pixel(bot_right, wcs)

            cut_out_ra_min = min(pix_top_left_ra,pix_top_right_ra,pix_bot_left_ra,pix_bot_right_ra)
            cut_out_ra_max = max(pix_top_left_ra,pix_top_right_ra,pix_bot_left_ra,pix_bot_right_ra)
            cut_out_dec_min = min(pix_top_left_dec,pix_top_right_dec,pix_bot_left_dec,pix_bot_right_dec)
            cut_out_dec_max = max(pix_top_left_dec,pix_top_right_dec,pix_bot_left_dec,pix_bot_right_dec)

            img = img[int(cut_out_ra_min):int(cut_out_ra_max), int(cut_out_dec_min):int(cut_out_dec_max)]

        #not yet implemented
        #else:
        #
        #    img_coord_lower = pixel_to_skycoord(0, 0, wcs)
        #    img_coord_upper = pixel_to_skycoord(len(img[0,:]), len(img[:,0]), wcs)

        if len(img>0) and len(img[0]>0):
            x_coords = np.linspace(bot_left.ra.degree, bot_right.ra.degree, len(img[:,0]))
            y_coords = np.linspace(bot_left.dec.degree, top_left.dec.degree, len(img[0,:]))
            
            zmin,zmax = [int(el) for el in ZScaleInterval().get_limits(img)]

            fig.add_trace(go.Heatmap(x=x_coords, y=y_coords, z=img, zmin=zmin, zmax=zmax, showscale=False), row=1, col=i+1)
        else:
            # This mainly happens when the WCS is off
            fig.add_trace(go.Heatmap(x=[], y=[], z=[], showscale=False), row=1, col=i+1)

        source_coords = []
        if filetype == 'diff' and display_type == 'individual':
            ### Get sky coordinates of sources
            for source in triplet['sources']:
                source_coord = pixel_to_skycoord(source['x'], source['y'], wcs)
                source_coords.append([source_coord.ra.degree, source_coord.dec.degree])
 
    fig.update_xaxes(matches='x')
    fig.update_yaxes(matches='y')

    if display_type == 'list':
        width = 900
        height = 300

    else:
        width = 1500
        height = 500

    fig.update_layout(
        autosize=False,
        width=width,
        height=height,
        margin=dict(
            l=0,
            r=0,
            b=0,
            t=0
        ),
        xaxis=dict(autorange='reversed'),
        shapes=[
            dict(type='circle', xref='x3', yref='y3',
                 x0=source[0]-5.0/3600, y0=source[1]-5.0/3600,
                 x1=source[0]+5.0/3600, y1=source[1]+5.0/3600,
                 line_color='white'
            ) 
        for source in source_coords]
    )

    figure = offline.plot(fig, output_type='div', show_link=False)
    plot_context['subplots'] = figure

    return plot_context


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


@register.filter(takes_context=True)
def has_images(context, galaxy):
    username = context['request'].user
    targ = get_target_from_galaxy(galaxy)
    if not targ:
        return False
    try:
        filepaths, filenames, dates, teles, filters, exptimes, psfxs, psfys = run_hook('find_images_from_snex1', targ.id, username)
    except:
        return False

    if filepaths:
        return True

    return False


@register.filter
def get_target_id(galaxy):
    targ = get_target_from_galaxy(galaxy)
    if not targ:
        return None
    return targ.id
