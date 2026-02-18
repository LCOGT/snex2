from tom_observations.models import ObservationRecord
import logging

logger = logging.getLogger(__name__)
def convert_cadence_strategy():
    obs_records = ObservationRecord.objects.all()
    for obs in obs_records:
        if obs.parameters.get('cadence_strategy') == 'SnexResumeCadenceAfterFailureStrategy':
            obs.parameters['cadence_strategy'] = 'ResumeCadenceAfterFailureStrategy'
            logger.info(f'Converted resume strategy for {obs.target} with obs id: {obs.id}')

        if obs.parameters.get('cadence_strategy') == 'SnexRetryCadenceAfterFailureStrategy':
            obs.parameters['cadence_strategy'] = 'RetryCadenceAfterFailureStrategy'
            logger.info(f'Converted single time observation strategy for {obs.target} with obs id: {obs.id}')