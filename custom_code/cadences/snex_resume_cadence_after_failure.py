import logging

from tom_observations.cadences.resume_cadence_after_failure import ResumeCadenceAfterFailureStrategy


logger = logging.getLogger(__name__)

class SnexResumeCadenceAfterFailureStrategy(ResumeCadenceAfterFailureStrategy):
    cadence_fields = {'cadence_frequency', 'reminder_date'}

    def update_observation_payload(self, observation_payload):
        """
        :param observation_payload: form parameters for facility observation form
        :type observation_payload: dict
        """
        existing_reminder_date = self.dynamic_cadence.cadence_parameters.get('reminder_date')
        
        if existing_reminder_date:
            observation_payload['reminder_date'] = existing_reminder_date
        return observation_payload
