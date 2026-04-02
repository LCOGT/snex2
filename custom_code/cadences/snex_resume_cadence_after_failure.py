import logging
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from tom_observations.models import ObservationGroup
from django.contrib.auth.models import Group
from guardian.models import GroupObjectPermission
from guardian.shortcuts import assign_perm
from tom_observations.facility import get_service_class
from datetime import timedelta
from django.utils import timezone
from dateutil.parser import parse
from tom_observations.models import ObservationRecord

from tom_observations.cadences.resume_cadence_after_failure import ResumeCadenceAfterFailureStrategy
from tom_targets.models import Target


logger = logging.getLogger(__name__)

class SnexCadencePermissionMixin:
    _target_ct = None
    _group_ct = None
    
    @classmethod
    def get_target_content_type(cls):
        if cls._target_ct is None:
            cls._target_ct = ContentType.objects.get_for_model(Target)
        return cls._target_ct
    
    @classmethod
    def get_group_content_type(cls):
        if cls._group_ct is None:
            cls._group_ct = ContentType.objects.get_for_model(ObservationGroup)
        return cls._group_ct
    
    def sync_permissions_to_records(self, new_observations):
        if not new_observations or settings.TARGET_PERMISSIONS_ONLY:
            return
        
        obs_group = self.dynamic_cadence.observation_group
        group_ct = self.get_group_content_type()
        group_perms = GroupObjectPermission.objects.filter(
            object_pk=obs_group.id,
            content_type=group_ct
        )
        
        logger.info(f'Found {group_perms.count()} group with permissions to observation group {obs_group.id}')
        
        if not group_perms.exists():
            target = new_observations[0].target
            logger.info(f'No group permissions on obs group, syncing from target permissions')
            target_ct = self.get_target_content_type()
            target_group_ids = GroupObjectPermission.objects.filter(
                object_pk=target.id,
                content_type=target_ct
            ).values_list('group_id', flat=True).distinct()
            
            logger.info(f'Found {target_group_ids.count()} target groups')
            groups = Group.objects.filter(id__in=target_group_ids)
        else:
            groups = Group.objects.filter(
                id__in=group_perms.values_list('group_id', flat=True).distinct()
            )
        
        if not group_perms.exists():
            for group in groups:
                assign_perm('tom_observations.view_observationgroup', group, obs_group)
                assign_perm('tom_observations.change_observationgroup', group, obs_group)
                assign_perm('tom_observations.delete_observationgroup', group, obs_group)
        
        for record in new_observations:
            for group in groups:
                assign_perm('tom_observations.view_observationrecord', group, record)
                assign_perm('tom_observations.change_observationrecord', group, record)
                assign_perm('tom_observations.delete_observationrecord', group, record)

class SnexResumeCadenceAfterFailureStrategy(SnexCadencePermissionMixin, ResumeCadenceAfterFailureStrategy):
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

    def run(self):
        # gets the most recent observation because the next observation is just going to modify these parameters
        last_obs = self.dynamic_cadence.observation_group.observation_records.order_by('-created').first()

        # Make a call to the facility to get the current status of the observation
        facility = get_service_class(last_obs.facility)()
        start_keyword, end_keyword = facility.get_start_end_keywords()
        facility.update_observation_status(last_obs.observation_id)  # Updates the DB record
        last_obs.refresh_from_db()  # Gets the record updates

        # Boilerplate to get necessary properties for future calls
        observation_payload = last_obs.parameters
        scheduled_end = last_obs.scheduled_end
        if not scheduled_end:
            logger.info(f'No observation end scheduled yet, falling back to end: {observation_payload[end_keyword]}')
            scheduled_end = parse(observation_payload[end_keyword])

        if isinstance(scheduled_end, str):
            scheduled_end = parse(scheduled_end)

        if timezone.is_naive(scheduled_end):
            scheduled_end = timezone.make_aware(scheduled_end)

        observation_payload['scheduled_end'] = scheduled_end
        logger.info(f'Scheduled observation end: {scheduled_end}')

        # Cadence logic
        # If the observation hasn't finished, do nothing
        if not last_obs.terminal:
            return
        elif last_obs.failed:  # If the observation failed
            # Submit next observation to be taken as soon as possible with the same window length
            cadence_frequency = self.dynamic_cadence.cadence_parameters.get('cadence_frequency')
            if not cadence_frequency:
                raise Exception(f'The {self.name} strategy requires a cadence_frequency cadence_parameter.')
            window_length = 24 if cadence_frequency > 24 else cadence_frequency
            now = timezone.now()
            observation_payload[start_keyword] = now.isoformat()
            observation_payload[end_keyword] = (now + timedelta(hours=window_length)).isoformat()
        
        else:  # If the observation succeeded
            # Advance window normally according to cadence parameters
            observation_payload = self.advance_window(
                observation_payload, start_keyword=start_keyword, end_keyword=end_keyword
            )

        observation_payload = self.update_observation_payload(observation_payload)

        # Submission of the new observation to the facility
        obs_type = last_obs.parameters.get('observation_type')
        form = facility.get_form(obs_type)(data=observation_payload)
        if form.is_valid():
            observation_ids = facility.submit_observation(form.observation_payload())
        else:
            logger.error(msg=f'Unable to submit next cadenced observation: {form.errors}')
            raise Exception(f'Unable to submit next cadenced observation: {form.errors}')

        # Creation of corresponding ObservationRecord objects for the observations
        new_observations = []
        for observation_id in observation_ids:
            # Create Observation record
            record = ObservationRecord.objects.create(
                target=last_obs.target,
                facility=facility.name,
                parameters=observation_payload,
                observation_id=observation_id
            )
            # Add ObservationRecords to the DynamicCadence
            self.dynamic_cadence.observation_group.observation_records.add(record)
            self.dynamic_cadence.observation_group.save()
            new_observations.append(record)

        # Update the status of the ObservationRecords in the DB
        for obsr in new_observations:
            facility = get_service_class(obsr.facility)()
            facility.update_observation_status(obsr.observation_id)
            obsr.refresh_from_db() # commit the updated observation status

        self.sync_permissions_to_records(new_observations)
        return new_observations

    def advance_window(self, observation_payload, start_keyword='start', end_keyword='end'):
        cadence_frequency = self.dynamic_cadence.cadence_parameters.get('cadence_frequency')
        if not cadence_frequency:
            raise Exception(f'The {self.name} strategy requires a cadence_frequency cadence_parameter.')
        advance_window_hours = cadence_frequency
        if settings.OBS_WINDOW_MINIMUM:
            min_window = settings.OBS_WINDOW_MINIMUM
        else:
            min_window = 24
        window_length = min_window if cadence_frequency > min_window else cadence_frequency

        new_start = observation_payload['scheduled_end'] + timedelta(hours=advance_window_hours)
        if new_start < timezone.now():  # Ensure that the new window isn't in the past
            new_start = timezone.now()
        new_end = new_start + timedelta(hours=window_length)
        observation_payload[start_keyword] = new_start.isoformat()
        observation_payload[end_keyword] = new_end.isoformat()

        return observation_payload
