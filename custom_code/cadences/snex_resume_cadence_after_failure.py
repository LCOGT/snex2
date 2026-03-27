import logging
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from tom_observations.models import ObservationGroup
from django.contrib.auth.models import Group
from guardian.models import GroupObjectPermission
from guardian.shortcuts import assign_perm

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
        
        logger.info(f'Group permissions on observation group: {group_perms}')
        
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
        new_observations = super().run()
        logger.info(f'Syncing permissions to group')
        self.sync_permissions_to_records(new_observations)
        return new_observations