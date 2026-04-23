import logging
from datetime import timedelta
from smtplib import SMTPException

from dateutil.parser import parse
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.mail import send_mail
from django.utils import timezone
from slack_sdk import WebClient

from tom_observations.cadences.retry_failed_observations import RetryFailedObservationsStrategy
from tom_observations.facility import get_service_class
from tom_observations.models import ObservationRecord

from custom_code.cadences.snex_resume_cadence_after_failure import SnexCadencePermissionMixin

logger = logging.getLogger(__name__)


def email_obs_update(obs):
    try:
        current_domain = Site.objects.get_current().domain
        base_url = f'https://{current_domain}'
        link_to_target = f'{base_url}/targets/{obs.target.id}'
        link_to_observationgroup = (
            f'{base_url}/observationgroup/{obs.observationgroup_set.first().id}'
        )
        username = obs.parameters.get('start_user', 'snex_secure')
        user = User.objects.get(username=username)

        send_mail(
            subject=f'Your observation of {obs.target.name} has been acquired!',
            message='',
            from_email=settings.SERVER_EMAIL,
            recipient_list=[user.email],
            html_message=(
                f'Your observation request for {obs.target.name} has been acquired. '
                f'You can view the data on the target page <a href="{link_to_target}">here</a> '
                f'or the observation record <a href="{link_to_observationgroup}">here</a>. '
                f'Resubmit another one time observation or switch to a repeating cadence.'
            ),
            fail_silently=False,
        )
    except (SMTPException, ConnectionRefusedError) as error:
        logger.error(f'Unable to send email: {error}')


def send_slack_notification(obs):
    current_domain = Site.objects.get_current().domain
    base_url = f'https://{current_domain}'
    link_to_target = f'{base_url}/targets/{obs.target.id}'
    link_to_observationgroup = (
        f'{base_url}/observationgroup/{obs.observationgroup_set.first().id}'
    )
    message = (
        f"{obs.parameters['observation_type']} observation of "
        f"<{link_to_target}|{obs.target.name}> "
        f"has been acquired. See observation info <{link_to_observationgroup}|here>."
    )

    client = WebClient(token=settings.SLACK_BOT_TOKEN)
    response = client.chat_postMessage(
        channel=settings.SLACK_OBS_CHANNEL,
        text=message,
    )
    return response["ok"]


class BaseRetryStrategy(SnexCadencePermissionMixin, RetryFailedObservationsStrategy):
    cadence_fields = {'cadence_frequency', 'reminder_date'}

    def run(self):
        records = self.dynamic_cadence.observation_group.observation_records.all().order_by('created')
        first_obs = records.first()
        last_obs = records.last()

        if not first_obs or not last_obs:
            return

        facility = get_service_class(last_obs.facility)()
        facility.update_observation_status(last_obs.observation_id)
        last_obs.refresh_from_db()

        if not last_obs.terminal:
            return

        if last_obs.status == 'COMPLETED':
            self.dynamic_cadence.active = False
            self.dynamic_cadence.save()
            logger.info(f'Observation {last_obs} completed; turned off dynamic cadence')
            return self.notify_success(last_obs)

        if last_obs.status == 'CANCELED':
            self.dynamic_cadence.active = False
            self.dynamic_cadence.save()
            logger.info(f'Observation {last_obs} was canceled, stopping dynamic cadence')
            return

        if not self.retry_observation(first_obs, last_obs, facility):
            self.dynamic_cadence.active = False
            self.dynamic_cadence.save()
            logger.info(
                f'Stopping retry cadence for observation group '
                f'{self.dynamic_cadence.observation_group.id}'
            )
            return

        return self.submit_retry_observation(first_obs, last_obs, facility)

    def notify_success(self, last_obs):
        try:
            email_obs_update(last_obs)
        except Exception:
            logger.exception('Email notification failed')

        try:
            send_slack_notification(last_obs)
        except Exception:
            logger.exception('Slack notification failed')

    def retry_observation(self, first_obs, last_obs, facility):
        return True

    def submit_retry_observation(self, first_obs, last_obs, facility):
        observation_payload = last_obs.parameters.copy()

        existing_reminder_date = self.dynamic_cadence.cadence_parameters.get('reminder_date')
        if existing_reminder_date:
            observation_payload['reminder_date'] = existing_reminder_date

        start_keyword, end_keyword = facility.get_start_end_keywords()
        observation_payload = self.advance_window(
            observation_payload,
            start_keyword=start_keyword,
            end_keyword=end_keyword,
            first_obs=first_obs,
            last_obs=last_obs,
            facility=facility,
        )

        if observation_payload is None:
            self.dynamic_cadence.active = False
            self.dynamic_cadence.save()
            logger.info(
                f'No retry window remaining for observation group '
                f'{self.dynamic_cadence.observation_group.id}; deactivated silently'
            )
            return

        obs_type = observation_payload.get('observation_type')
        form = facility.get_form(obs_type)(observation_payload)

        if not form.is_valid():
            logger.error(
                msg=f'Unable to submit next observation: {form.errors} '
                f'for ObservationRecord.id: {last_obs.id}'
            )
            raise Exception(f'Unable to submit next observation: {form.errors}')

        observation_ids = facility.submit_observation(form.observation_payload())
        new_observations = []

        for observation_id in observation_ids:
            record = ObservationRecord.objects.create(
                target=last_obs.target,
                facility=facility.name,
                parameters=observation_payload,
                observation_id=observation_id,
            )
            self.dynamic_cadence.observation_group.observation_records.add(record)
            new_observations.append(record)

        self.dynamic_cadence.observation_group.save()

        for obsr in new_observations:
            facility.update_observation_status(obsr.observation_id)
            obsr.refresh_from_db()

        self.sync_permissions_to_records(new_observations)
        return new_observations

    def advance_window(
        self,
        observation_payload,
        start_keyword='start',
        end_keyword='end',
        first_obs=None,
        last_obs=None,
        facility=None,
    ):
        cadence_frequency = self.dynamic_cadence.cadence_parameters.get('cadence_frequency')
        if cadence_frequency is None:
            raise Exception(
                f'The {self.name} strategy requires a cadence_frequency cadence_parameter.'
            )

        min_window = settings.OBS_WINDOW_MINIMUM or 24
        window = min(cadence_frequency, min_window)

        new_start = timezone.now()
        new_end = new_start + timedelta(hours=window)

        observation_payload[start_keyword] = new_start.isoformat()
        observation_payload[end_keyword] = new_end.isoformat()
        return observation_payload


class SnexRetryFailedObservationsStrategy(BaseRetryStrategy):
    """
    Retry indefinitely until the observation succeeds.
    """
    pass


class SnexRetryUntilDeadlineStrategy(BaseRetryStrategy):
    """
    Retry in short windows until either the observation succeeds, or the
    original cadence_frequency interval has elapsed.
    """

    def retry_observation(self, first_obs, last_obs, facility):
        deadline = self.get_deadline(first_obs, facility)
        return timezone.now() < deadline

    def advance_window(
        self,
        observation_payload,
        start_keyword='start',
        end_keyword='end',
        first_obs=None,
        last_obs=None,
        facility=None,
    ):
        cadence_frequency = self.dynamic_cadence.cadence_parameters.get('cadence_frequency')
        if cadence_frequency is None:
            raise Exception(
                f'The {self.name} strategy requires a cadence_frequency cadence_parameter.'
            )

        min_window = settings.OBS_WINDOW_MINIMUM or 24
        window = min(cadence_frequency, min_window)

        deadline = self.get_deadline(first_obs, facility)
        new_start = timezone.now()

        if new_start >= deadline:
            return None

        new_end = min(new_start + timedelta(hours=window), deadline)

        if new_end <= new_start:
            return None

        observation_payload[start_keyword] = new_start.isoformat()
        observation_payload[end_keyword] = new_end.isoformat()
        return observation_payload

    def get_deadline(self, first_obs, facility):
        cadence_frequency = self.dynamic_cadence.cadence_parameters.get('cadence_frequency')
        if cadence_frequency is None:
            raise Exception(
                f'The {self.name} strategy requires a cadence_frequency cadence_parameter.'
            )

        start_keyword, _ = facility.get_start_end_keywords()
        start_value = first_obs.parameters.get(start_keyword)

        if not start_value:
            raise Exception(
                f'Could not determine original start time for '
                f'ObservationRecord.id={first_obs.id}'
            )

        original_start = parse(start_value) if isinstance(start_value, str) else start_value
        if timezone.is_naive(original_start):
            original_start = timezone.make_aware(original_start)

        return original_start + timedelta(hours=cadence_frequency)