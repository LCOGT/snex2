from django.shortcuts import render
from django.conf import settings
from django.http import HttpResponse
from django.db import transaction
from django.db.models import F
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import Group
from django.views.generic import ListView
from django.views.generic.base import TemplateView
from guardian.shortcuts import assign_perm
import json
import os
from astropy.io import fits
import sep
from datetime import datetime, timedelta
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventSequence, EventLocalization
from gw.models import GWFollowupGalaxy
from gw.forms import GWGalaxyObservationForm
from gw.treasure_map_utils import build_tm_pointings, submit_tm_pointings
from tom_common.hooks import run_hook
from tom_targets.models import Target
from tom_observations.facility import get_service_class
from tom_observations.models import ObservationRecord, ObservationGroup, DynamicCadence
from tom_dataproducts.models import ReducedDatum
from custom_code.utils import format_form_errors
import logging

logger = logging.getLogger(__name__)


def pipeline_image(path):
    local = os.path.join(settings.FITS_DIR, path.replace(settings.LSC_DIR, '').replace('/supernova/data/', '').lstrip('/'))
    return local if os.path.isfile(local) else local + '.fz'

BASE_DIR = settings.BASE_DIR


class GWFollowupGalaxyListView(LoginRequiredMixin, ListView):

    template_name = 'gw/galaxy_list.html'
    paginate_by = 30
    model = GWFollowupGalaxy
    context_object_name = 'galaxies'

    def get_queryset(self):
        sequence = EventSequence.objects.get(id=self.kwargs['id'])
        loc = sequence.localization
        galaxies = GWFollowupGalaxy.objects.filter(eventlocalization=loc)
        galaxies = galaxies.annotate(name=F("id"))
        galaxies = galaxies.order_by('-score')

        return galaxies

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context['sequence'] = EventSequence.objects.get(id=self.kwargs['id'])
        context['superevent_id'] = EventSequence.objects.get(id=self.kwargs['id']).nonlocalizedevent.event_id
        context['galaxy_count'] = len(self.get_queryset())
        context['obs_form'] = GWGalaxyObservationForm()
        return context


class EventSequenceGalaxiesTripletView(ListView, LoginRequiredMixin):

    template_name = 'gw/galaxy_observations.html'
    paginate_by = 5
    model = GWFollowupGalaxy
    context_object_name = 'galaxies'

    def get_queryset(self):
        sequence = EventSequence.objects.get(id=self.kwargs['id'])
        loc = sequence.localization
        galaxies = GWFollowupGalaxy.objects.filter(eventlocalization=loc)
        galaxies = galaxies.annotate(name=F("id"))

        return galaxies.order_by('-score')
    
    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        sequence = EventSequence.objects.get(id=self.kwargs['id'])
        context['sequence'] = sequence
        galaxies = self.get_queryset()
        context['galaxy_count'] = len(galaxies)

        context['superevent_id'] = sequence.nonlocalizedevent.event_id 
        context['superevent_index'] = sequence.nonlocalizedevent.id

        rows = []
        for galaxy in context['object_list']:
            triplets = []
            subtractions = ReducedDatum.objects.filter(
                target__in=Target.objects.filter(gwfollowupgalaxy_id=galaxy.id), data_type='photometry',
                value__background_subtracted=True, value__has_key='template_image').order_by('timestamp')
            for datum in subtractions:
                diff_file = pipeline_image(datum.value['difference_image'])
                original = os.path.join(os.path.dirname(diff_file), datum.value['basename'] + '.fits')
                triplets.append({
                    'obsdate': datum.timestamp.date(),
                    'filter': datum.value.get('filter'),
                    'exposure_time': datum.value.get('exptime'),
                    'original': {'filename': original if os.path.isfile(original) else original + '.fz'},
                    'template': {'filename': pipeline_image(datum.value['template_image'])},
                    'diff': {'filename': diff_file},
                })
            if triplets:
                rows.append({'galaxy': galaxy, 'triplets': triplets})

        context['rows'] = rows

        return context

#this is not yet implemented
class GWFollowupGalaxyTripletView(TemplateView, LoginRequiredMixin):

    template_name = 'gw/galaxy_observations_individual.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        galaxy = GWFollowupGalaxy.objects.get(id=self.kwargs['id'])
        context['galaxy'] = galaxy

        loc = galaxy.eventlocalization
        context['superevent_id'] = loc.nonlocalizedevent.event_id 
        context['superevent_index'] = loc.nonlocalizedevent.id

        rows = []

        #TODO: Populate this dynamically

        triplets = [{
            'obsdate': '2023-04-19',
            'filter': 'g',
            'exposure_time': 200,
            'original': {'filename': os.path.join(BASE_DIR, settings.FITS_DIR,'gw','obs.fits')},
            'template': {'filename': os.path.join(BASE_DIR, settings.FITS_DIR,'gw','ref.fits')},
            'diff': {'filename': os.path.join(BASE_DIR, settings.FITS_DIR,'gw','sub.fits')}
        }]

        ### Run SExtractor to get sources to plot
        for triplet in triplets:
            hdu = fits.open(triplet['diff']['filename'])
            img = hdu[0].data
            hdu.close()

            bkg = sep.Background(img.byteswap().newbyteorder())
            sources = sep.extract(img-bkg, 5.0, err=bkg.globalrms)
            triplet['sources'] = sources

        context['triplets'] = triplets

        return context


def submit_galaxy_observations_view(request):

    ### Get list of GWFollowupGalaxy ids from the request and create Targets
    galaxy_ids = json.loads(request.GET['galaxy_ids'])['galaxy_ids']
    galaxies = GWFollowupGalaxy.objects.filter(id__in=galaxy_ids)

    try:
        failed_obs = []
        all_pointings = []
        with transaction.atomic():
            for galaxy in galaxies:
                newtarget, created = Target.objects.get_or_create(
                        name=galaxy.catalog_objname,
                        #ra=galaxy.ra,
                        #dec=galaxy.dec,
                        type='SIDEREAL'
                )

                if created:
                    newtarget.ra = galaxy.ra
                    newtarget.dec = galaxy.dec
                    newtarget.gwfollowupgalaxy_id = galaxy.id
                    newtarget.save()
                    gw = Group.objects.get(name='GWO4')
                    assign_perm('custom_code.view_target', gw, newtarget)
                    assign_perm('custom_code.change_target', gw, newtarget)
                    assign_perm('custom_code.delete_target', gw, newtarget)

                ### Create and submit the observation requests
                form_data = {'name': newtarget.name,
                             'target_id': newtarget.id,
                             'facility': 'LCO',
                             'observation_type': 'IMAGING'
                }

                observing_parameters = {}
                observing_parameters['ipp_value'] = float(request.GET['ipp_value'])
                observing_parameters['max_airmass'] = 2.0 #TODO: Add form field for this?
                observing_parameters['cadence_strategy'] = 'SnexRetryFailedObservationsStrategy'
                observing_parameters['cadence_frequency'] = 24 #TODO: This is from SNEx1, change?
                observing_parameters['cadence_frequency_days'] = 1.0 #TODO: This is from SNEx1, change?
                observing_parameters['reminder'] = 1.0
                observing_parameters['facility'] = 'LCO'
                observing_parameters['name'] = newtarget.name
                observing_parameters['target_id'] = newtarget.id
                observing_parameters['delay_start'] = False
                observing_parameters['instrument_type'] = request.GET['instrument_type']
                observing_parameters['observation_type'] = 'IMAGING'
                observing_parameters['observation_mode'] = request.GET['observation_mode']
                observing_parameters['site'] = 'any'
                observing_parameters['min_lunar_distance'] = 20.0
                observing_parameters['proposal'] = 'KEY2020B-001'

                now = datetime.utcnow()
                observing_parameters['start'] = datetime.strftime(now, '%Y-%m-%dT%H:%M:%S')
                if 'RAPID' in request.GET['observation_mode'] or 'CRITICAL'in request.GET['observation_mode']:
                    observing_parameters['end'] = datetime.strftime(now + timedelta(days=1), '%Y-%m-%dT%H:%M:%S')
                else:
                    observing_parameters['end'] = datetime.strftime(now + timedelta(days=float(request.GET['epochs'])), '%Y-%m-%dT%H:%M:%S') #TODO: Check if this is actually what we want

                cadence = {'cadence_strategy': observing_parameters['cadence_strategy'],
                           'cadence_frequency': observing_parameters['cadence_frequency']
                }

                filters = request.GET['filters'].split(',')
                for f in filters:
                    if f in ['g', 'r', 'i']:
                        f += 'p'
                    elif f == 'z':
                        f += 's'
                    observing_parameters[f] = [float(request.GET['exposure_time']), int(request.GET['exposures_per_epoch']), 1]

                form_data['cadence'] = cadence
                form_data['observing_parameters'] = observing_parameters

                facility = get_service_class('LCO')()
                form = facility.get_form(form_data['observation_type'])(observing_parameters)
                if form.is_valid():
                    observation_errors = facility.validate_observation(form.observation_payload())

                    if observation_errors:
                        logger.error(msg=f'Unable to submit observation for {newtarget.name}: {observation_errors}')
                        failed_obs.append(newtarget.name)
                        continue
                        #response_data = {'failure': 'Unable to submit observation for {}'.format(newtarget.name)}

                else:
                    logger.error(msg=f'Unable to submit observation for {newtarget.name}: {format_form_errors(form.errors)}')
                    failed_obs.append(newtarget.name)
                    continue
                    #response_data = {'failure': 'Unable to submit observation'}

                new_observations = []
                # Create Observation record
                record = ObservationRecord.objects.create(
                    target=newtarget,
                    facility=facility.name,
                    parameters=form.serialize_parameters(),
                    observation_id='template'
                )
                # Add the request user
                record.parameters['start_user'] = request.user.username
                record.save()
                new_observations.append(record)
        
                if len(new_observations) > 1 or form_data.get('cadence'):
                    observation_group = ObservationGroup.objects.create(name=form_data['name'])
                    observation_group.observation_records.add(*new_observations)
                    assign_perm('tom_observations.view_observationgroup', request.user, observation_group)
                    assign_perm('tom_observations.change_observationgroup', request.user, observation_group)
                    assign_perm('tom_observations.delete_observationgroup', request.user, observation_group)

                    if form_data.get('cadence'):
                        DynamicCadence.objects.create(
                            observation_group=observation_group,
                            cadence_strategy=cadence.get('cadence_strategy'),
                            cadence_parameters={'cadence_frequency': cadence.get('cadence_frequency')},
                            active=True
                        )

                groups = Group.objects.filter(name='GWO4')
                for record in new_observations:
                    assign_perm('tom_observations.view_observationrecord', groups, record)
                    assign_perm('tom_observations.change_observationrecord', groups, record)
                    assign_perm('tom_observations.delete_observationrecord', groups, record)

                snex_id = 1

                if len(new_observations) > 1 or form_data.get('cadence'):
                    observation_group.name = str(snex_id)
                    observation_group.save()

                    for record in new_observations:
                        record.parameters['name'] = snex_id
                        record.save()

                ### Submit pointing to TreasureMap
                #pointings = build_tm_pointings(newtarget, observing_parameters)

                #all_pointings += pointings

            #submitted = submit_tm_pointings(galaxy.eventlocalization.sequences.first(), all_pointings)
            #if not submitted:
            #    logger.error('Submitting to Treasure Map failed for these observations')

        if not failed_obs:
            failed_obs_str = 'All observations submitted successfully'
        else:
            failed_obs_str = 'Observations failed to submit for the following galaxies: ' + ','.join(failed_obs)
        response_data = {'success': 'Submitted',
                         'failed_obs': failed_obs_str}

    except Exception as e:
        logger.error('Creating galaxy Target objects and scheduling observations failed with error: {}'.format(e))
        response_data = {'failure': 'Creating galaxy Target objects and scheduling observations failed'}

    return HttpResponse(json.dumps(response_data), content_type='application/json')


def cancel_galaxy_observations_view(request):

    ### Get list of GWFollowupGalaxy ids from the request and create Targets
    try:
        galaxy_ids = json.loads(request.GET['galaxy_ids'])
        with transaction.atomic():
            run_hook('cancel_gw_obs', galaxy_ids=galaxy_ids)

        response_data = {'success': 'Canceled'}

    except Exception as e:
        logger.error('Canceling follow-up observations failed with error: {}'.format(e))
        response_data = {'failure': 'Could not cancel follow-up observations for these galaxies'}

    return HttpResponse(json.dumps(response_data), content_type='application/json')
