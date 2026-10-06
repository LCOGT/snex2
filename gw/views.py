from django.http import HttpResponse
from django.db import transaction
from django.db.models import F, Q
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import Group
from django.views.generic import ListView
from guardian.shortcuts import assign_perm
import json
import os
from datetime import datetime, timedelta
from tom_nonlocalizedevents.models import EventSequence
from gw.models import GWFollowupGalaxy
from gw.forms import GWGalaxyObservationForm
from tom_common.hooks import run_hook
from tom_targets.models import Target
from tom_observations.facility import get_service_class
from tom_observations.models import ObservationRecord, ObservationGroup, DynamicCadence
from tom_dataproducts.models import DataProduct, PhotometryReducedDatum
from custom_code.thumbnails import cached_frame
from custom_code.utils import format_form_errors, unsubtracted_q
import logging

logger = logging.getLogger(__name__)


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


class EventSequenceGalaxiesImagesView(LoginRequiredMixin, ListView):

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
            images = []
            photometry = PhotometryReducedDatum.objects.filter(
                target__in=Target.objects.filter(Q(gwfollowupgalaxy_id=galaxy.id) | Q(name=galaxy.catalog_objname)),
                value__has_key='basename')
            subtractions = {datum.value['basename']: datum.value for datum in photometry.filter(value__background_subtracted=True)}
            for datum in photometry.filter(unsubtracted_q()).order_by('timestamp'):
                try:
                    filenames = [cached_frame(datum.value['basename'])]
                except OSError:
                    continue
                subtraction = subtractions.get(datum.value['basename'], {})
                for key in ('template_image', 'difference_image'):
                    if not subtraction.get(key):
                        continue
                    product = DataProduct.objects.filter(
                        product_id=os.path.basename(subtraction[key]).split('.fits')[0], data_product_type=key).first()
                    if product and product.data and os.path.isfile(product.data.path):
                        filenames.append(product.data.path)
                images.append({
                    'obsdate': datum.timestamp.date(),
                    'filter': datum.bandpass,
                    'exposure_time': datum.value.get('exptime'),
                    'filenames': filenames,
                })
            if images:
                rows.append({'galaxy': galaxy, 'images': images})

        context['rows'] = rows

        return context

@login_required
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


@login_required
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
