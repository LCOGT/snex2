import logging
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from tom_observations.models import ObservationGroup
from django.contrib.auth.models import Group
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
        if not group_perms.exists():
            target = new_observations[0].target
            target_ct = ContentType.objects.get_for_model(target.__class__)
            target_group_ids = GroupObjectPermission.objects.filter(
                object_pk=target.id,
                content_type=target_ct
            ).values_list('group_id', flat=True).distinct()
            groups = Group.objects.filter(id__in=target_group_ids)
            for group in groups:
                assign_perm('tom_observations.view_observationgroup', group, obs_group)
                assign_perm('tom_observations.change_observationgroup', group, obs_group)
                assign_perm('tom_observations.delete_observationgroup', group, obs_group)
                for record in new_observations:
                    assign_perm('tom_observations.view_observationrecord', group, record)
                    assign_perm('tom_observations.change_observationrecord', group, record)
                    assign_perm('tom_observations.delete_observationrecord', group, record)
        else:
            group_ids = group_perms.values_list('group_id', flat=True).distinct()
            groups = Group.objects.filter(id__in=group_ids)
            for group in groups:
                for record in new_observations:
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
        new_observations = super().run()
        self.sync_permissions_to_records(new_observations)
        return new_observations