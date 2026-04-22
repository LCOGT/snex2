from dateutil.parser import parse
from datetime import timedelta
from smtplib import SMTPException
from django.conf import settings
from django.utils import timezone

from tom_observations.models import ObservationRecord
from tom_observations.cadences.retry_failed_observations import RetryFailedObservationsStrategy
from tom_observations.facility import get_service_class
from custom_code.cadences.snex_resume_cadence_after_failure import SnexCadencePermissionMixin

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.mail import send_mail
from slack_sdk import WebClient

import logging

logger = logging.getLogger(__name__)

def email_obs_update(obs):
    try:
        # Send email to notify submitting user that observation was obtained
        current_domain = Site.objects.get_current().domain
        link_to_target = f'{current_domain}/targets/{obs.target.id}'
        link_to_observationgroup = f'{current_domain}/observationgroup/{obs.observationgroup_set.first().id}'
        username = obs.parameters.get('start_user', 'snex_secure')
        user = User.objects.get(username = username)

        send_mail(
            subject=f'Your observation of {obs.target.name} has been acquired!',
            message='',  
            from_email=settings.SERVER_EMAIL,
            recipient_list=[user.email],
            html_message= f'Your observation request for {obs.target.name} been acquired. You can view the data on the target page <a href="{link_to_target}">here</a> or the observation record <a href="{link_to_observationgroup}">here</a>. Resubmit another one time observation or switch to a repeating cadence.',
            fail_silently=False)
    except (SMTPException, ConnectionRefusedError) as error:
        logger.error(f'Unable to send email: {error}')

def send_slack_notification(obs):
    current_domain = Site.objects.get_current().domain

    link_to_target = f"https://{current_domain}/targets/{obs.target.id}"
    link_to_observationgroup = f'{current_domain}/observationgroup/{obs.observationgroup_set.first().id}'
    message = (
        f"{obs.parameters['observation_type']} observation of "
        f"<{link_to_target}|{obs.target.name}> "
        f"has been acquired. See observation info <{link_to_observationgroup}|here>."
    )

    client = WebClient(token=settings.SLACK_BOT_TOKEN)
    response = client.chat_postMessage(
        channel=settings.SLACK_OBS_CHANNEL,
        text=message
    )

    return response["ok"]

class SnexRetryFailedObservationsStrategy(SnexCadencePermissionMixin, RetryFailedObservationsStrategy):
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
            try:
                email_obs_update(last_obs)
            except Exception:
                logger.exception("Email notification failed")

            try:
                send_slack_notification(last_obs)
            except Exception:
                logger.exception("Slack notification failed")

            logger.info(f'Observation {last_obs} complete, emailed user and turned off dynamic cadence')
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
            logger.error(msg=f'Unable to submit next observation: {form.errors} for ObservationRecord.id: {last_obs.id}')
            raise Exception(f'Unable to submit next observation: {form.errors}')

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
            obsr.refresh_from_db()

        self.sync_permissions_to_records(new_observations)
        return new_observations

    def advance_window(self, observation_payload, start_keyword='start', end_keyword='end'):
        cadence_frequency = self.dynamic_cadence.cadence_parameters.get('cadence_frequency')
        if not cadence_frequency:
            raise Exception(f'The {self.name} strategy requires a cadence_frequency cadence_parameter.')
        if settings.OBS_WINDOW_MINIMUM:
            min_window = settings.OBS_WINDOW_MINIMUM
        else:
            min_window = 24
        window = min_window if cadence_frequency > min_window else cadence_frequency
        
        new_start = timezone.now()
        new_end = new_start + timedelta(hours=window)
        observation_payload[start_keyword] = new_start.isoformat()
        observation_payload[end_keyword] = new_end.isoformat()

        return observation_payload
