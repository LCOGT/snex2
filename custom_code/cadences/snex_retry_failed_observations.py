import logging

from datetime import datetime, timedelta
from tom_observations.models import ObservationRecord, ObservationGroup, DynamicCadence
from tom_observations.cadences.retry_failed_observations import RetryFailedObservationsStrategy
from tom_observations.facility import get_service_class

from django.conf import settings
from urllib.parse import urlencode
from dateutil.parser import parse

logger = logging.getLogger(__name__)

class SnexRetryFailedObservationsStrategy(RetryFailedObservationsStrategy):

    def run(self):
        last_obs = self.dynamic_cadence.observation_group.observation_records.order_by('-created').first()

        facility = get_service_class(last_obs.facility)()
        facility.update_observation_status(last_obs.observation_id)  # Updates the DB record
        last_obs.refresh_from_db() 

        if not last_obs.terminal:
            return
        elif last_obs.status == 'COMPLETED':
            obs_group = last_obs.observationgroup_set.first()
            dynamic_cadence = DynamicCadence.objects.get(observation_group=obs_group)
            dynamic_cadence.active = False
            dynamic_cadence.save()
            logger.info(f'observation complete, turned off dynamic cadence')

        failed_observations = [obsr for obsr
                               in self.dynamic_cadence.observation_group.observation_records.all()
                               if obsr.failed]

        new_observations = []
        for obs in failed_observations:
            observation_payload = obs.parameters

            facility = get_service_class(obs.facility)()
            start_keyword, end_keyword = facility.get_start_end_keywords()
            observation_payload = self.advance_window(
                observation_payload, start_keyword=start_keyword, end_keyword=end_keyword
            )
            obs_type = obs.parameters.get('observation_type', None)
            form = facility.get_form(obs_type)(observation_payload)
            form.is_valid()
            observation_ids = facility.submit_observation(form.observation_payload())

            for observation_id in observation_ids:
                # Create Observation record
                record = ObservationRecord.objects.create(
                    target=obs.target,
                    facility=facility.name,
                    parameters=observation_payload,
                    observation_id=observation_id
                )
                self.dynamic_cadence.observation_group.observation_records.add(record)
                self.dynamic_cadence.observation_group.save()
                new_observations.append(record)

        return new_observations