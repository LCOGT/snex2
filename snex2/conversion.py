from tom_observations.models import ObservationRecord, DynamicCadence
import logging

logger = logging.getLogger(__name__)
def convert_cadence_strategy():
    obs_records = ObservationRecord.objects.all()
    for obs in obs_records:
        if obs.parameters.get('cadence_strategy') == 'SnexResumeCadenceAfterFailureStrategy':
            obs.parameters['cadence_strategy'] = 'ResumeCadenceAfterFailureStrategy'
            logger.info(f'Converted resume strategy for {obs.target} with obs id: {obs.id}')

        if obs.parameters.get('cadence_strategy') == 'SnexRetryFailedObservationsStrategy':
            obs.parameters['cadence_strategy'] = 'RetryFailedObservationsStrategy'
            logger.info(f'Converted single time observation strategy for {obs.target} with obs id: {obs.id}')
    
    cads = DynamicCadence.objects.all()
    for cad in cads:
        if cad.cadence_strategy == 'SnexResumeCadenceAfterFailureStrategy':
            cad.cadence_strategy = 'ResumeCadenceAfterFailureStrategy'
            logger.info(f'Converted cadence strategy for {cad.observation_group_id} with cad id: {cad.id}')

        if cad.cadence_strategy == 'SnexRetryFailedObservationsStrategy':
            cad.cadence_strategy = 'RetryFailedObservationsStrategy'
            logger.info(f'Converted single time cadence strategy for {cad.target} with cad id: {cad.id}')
    