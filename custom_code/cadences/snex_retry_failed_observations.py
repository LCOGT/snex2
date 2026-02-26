from tom_observations.models import ObservationRecord
from tom_observations.cadences.retry_failed_observations import RetryFailedObservationsStrategy
from tom_observations.facility import get_service_class
import logging

logger = logging.getLogger(__name__)

class SnexRetryFailedObservationsStrategy(RetryFailedObservationsStrategy):
    cadence_fields = {'cadence_frequency', 'reminder_date'}

    def run(self):
        records = self.dynamic_cadence.observation_group.observation_records.all().order_by('-created')
        last_obs = records.first()

        if not last_obs:
            return

        facility_class = get_service_class(last_obs.facility)
        facility = facility_class()
        facility.update_observation_status(last_obs.observation_id)
        last_obs.refresh_from_db()

        if not last_obs.terminal:
            return
        elif last_obs.status == 'COMPLETED':
            self.dynamic_cadence.active = False
            self.dynamic_cadence.save()
            logger.info(f'Observation {last_obs} complete, turned off dynamic cadence')
            return

        if not last_obs.failed:
            return

        observation_payload = last_obs.parameters.copy()
        existing_reminder_date = self.dynamic_cadence.cadence_parameters.get('reminder_date')
        if existing_reminder_date:
            observation_payload['reminder_date'] = existing_reminder_date

        start_keyword, end_keyword = facility.get_start_end_keywords()
        observation_payload = self.advance_window(
            observation_payload, start_keyword=start_keyword, end_keyword=end_keyword
        )
        
        obs_type = observation_payload.get('observation_type')
        form = facility.get_form(obs_type)(observation_payload)
        
        if not form.is_valid():
            logger.error(f"Form validation failed: {form.errors}")
            return


        observation_ids = facility.submit_observation(form.observation_payload())
        new_observations = []
    
        for observation_id in observation_ids:
            record = ObservationRecord.objects.create(
                target=last_obs.target,
                facility=facility.name,
                parameters=observation_payload,
                observation_id=observation_id
            )
            self.dynamic_cadence.observation_group.observation_records.add(record)
            new_observations.append(record)

        self.dynamic_cadence.observation_group.save()

        for obsr in new_observations:
            facility.update_observation_status(obsr.observation_id)

        return new_observations
