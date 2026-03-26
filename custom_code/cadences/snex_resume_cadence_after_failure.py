import logging
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from tom_observations.models import ObservationGroup
from guardian.models import GroupObjectPermission
from guardian.shortcuts import assign_perm

from tom_observations.cadences.resume_cadence_after_failure import ResumeCadenceAfterFailureStrategy


logger = logging.getLogger(__name__)

class SnexCadencePermissionMixin:
    def sync_permissions_to_records(self, new_observations):
        if not new_observations or settings.TARGET_PERMISSIONS_ONLY:
            return
        obs_group = self.dynamic_cadence.observation_group
        group_ct = ContentType.objects.get_for_model(ObservationGroup)
        group_perms = GroupObjectPermission.objects.filter(
            object_pk=obs_group.id,
            content_type=group_ct
        )
        for record in new_observations:
            for gop in group_perms:
                assign_perm(gop.permission.codename, gop.group, record)

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
        new_observations = super().run()
        self.sync_permissions_to_records(new_observations)
        return new_observations