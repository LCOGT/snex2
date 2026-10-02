import os
from gw.models import GWFollowupGalaxy
from tom_common.hooks import run_hook
from tom_targets.models import Target
from tom_observations.models import ObservationRecord
from tom_nonlocalizedevents.models import EventSequence
from custom_code.scheduling import cancel_observation
import logging
from django.conf import settings


logger = logging.getLogger(__name__)

def cancel_gw_obs(galaxy_ids=[], sequence_id=None):
    """
    Hook to cancel observations for galaxies corresponding to a GW EventSequence
    Takes as input either a list of GWFollowupGalaxy IDs or an EventSequence ID
    """

    if not galaxy_ids and not sequence_id:
        logger.warning('Must provide either list of galaxy ids or an EventSequence id to cancel observations')
        return

    if galaxy_ids:
        galaxies = GWFollowupGalaxy.objects.filter(id__in=galaxy_ids)

    elif sequence_id:
        sequence = EventSequence.objects.get(id=sequence_id)
        # Get galaxies associated with this sequence
        galaxies = GWFollowupGalaxy.objects.filter(eventlocalization=sequence.localization)

    targets = Target.objects.filter(gwfollowupgalaxy_id__in=[g.id for g in galaxies])

    for target in targets:
        ### Cancel any observation requests for this target
        templates = ObservationRecord.objects.filter(target=target, status='PENDING')
        for template in templates:
            canceled = cancel_observation(template)
            if not canceled:
                response_data = {'failure': 'Canceling sequence failed'}
                logger.inf(f'failure to cancel sequence: {response_data}')            
            obs_group = template.observationgroup_set.first()

    if galaxy_ids:
        logger.info('Finished canceling GW follow-up observations for galaxies {}'.format(galaxy_ids))
    else:
        logger.info('Finished canceling GW follow-up observations for sequence {}'.format(sequence_id))
