from django.db import transaction
from guardian.models import GroupObjectPermission
from guardian.shortcuts import get_groups_with_perms, assign_perm
from django.contrib.auth.models import Group
from django.conf import settings
from datetime import datetime, timedelta
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from django_comments.models import Comment
from guardian.shortcuts import assign_perm
from tom_observations.models import ObservationRecord, ObservationGroup, DynamicCadence
from tom_observations.facility import get_service_class
import logging

logger = logging.getLogger(__name__)
def change_obs_from_scheduling(action, obs_id, user, data):
    '''
    Logic of the scheduling page
    params:
    action: str, modify, continue, stop
    obs_id: ObservationRecord id
    data: cleaned data from PhotSchedulingForm or SpecSchedulingForm
    '''
    obs = ObservationRecord.objects.get(id=obs_id)

    with transaction.atomic():
        if action == 'stop':
            logger.info(f'User {user.username} stopping sequence for obs {obs_id}')
            return _stop_sequence(obs, user, data)
        elif action == 'continue':
            logger.info(f'User {user.username} continuing sequence for obs {obs_id}')
            return _continue_sequence(obs, user, data)
        elif action == 'modify':
            logger.info(f'User {user.username} modifying sequence for obs {obs_id}')
            return _modify_sequence(obs, user, data)
        
        return None
    
def _stop_sequence(obs, user, data):
    logger.info(f'Stopping Sequence {obs.id} for target {obs.target.id}')
    
    ## Cancel observation request in LCO portal
    canceled = cancel_observation(obs)
    if not canceled:
        return {'failure': 'The facility (LCO) rejected the cancellation request.'}
    
    obs_group = obs.observationgroup_set.first()
    comment = data.get('comment', '')

    if comment and obs_group:
        save_comments(comment, obs_group.id, user)
        logger.info(f'Comment {comment} by user {user} saved for obs group {obs_group.id}')

    return {'success': 'Stopped'}

def save_comments(comment_text, object_id, user, model_name='observationgroup'):
    try:
        model_map = {
            'observationgroup': 'observationgroup',
            'spec': 'reduceddatum',
            'targets': 'snextarget'
        }
        actual_model = model_map.get(model_name, model_name)
        content_type = ContentType.objects.get(model=actual_model)

        newcomment, created = Comment.objects.get_or_create(
            object_pk=str(object_id),
            content_type=content_type,
            user=user,
            comment=comment_text,
            defaults={
                'user_name': user.username,
                'user_email': user.email,
                'submit_date': timezone.now(),
                'is_public': True,
                'is_removed': False,
                'site_id': getattr(settings, 'SITE_ID')
            }
        )
        
        if created:
            logger.info(f'New comment created for {actual_model} {object_id}')
        else:
            logger.info(f'Comment already created for {actual_model} {object_id}')
            
        return newcomment
    except Exception as e:
        logger.error(f'Comment save failed: {e}')
        return False

def cancel_observation(obs):
    obs_group = obs.observationgroup_set.first()
    if not obs_group:
        return False

    facility_class = get_service_class(obs.facility)
    facility = facility_class()

    if not getattr(obs, 'terminal', False):
        success = facility.cancel_observation(obs.observation_id)
        if not success:
            return False

        obs.status = 'CANCELED'
        obs.save()
    
    try:
        dynamic_cadence = DynamicCadence.objects.get(observation_group=obs_group)
        dynamic_cadence.active = False
        dynamic_cadence.save()
    except DynamicCadence.DoesNotExist:
        logger.warning(f"No active cadence found for group {obs_group.id}")

    first_obs = obs_group.observation_records.order_by('created').first()
    if first_obs:
        first_obs.parameters['sequence_end'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
        first_obs.save()
    
    return True
    
def _continue_sequence(obs, user, data):
    logger.info(f'Continuing Sequence {obs.id} for target {obs.target.id} as-is')
    
    for key in ['ipp_value', 'max_airmass', 'cadence_frequency_days', 'U', 'B', 'V', 'gp', 'rp', 'ip', 'zs', 'w', 'muscat_filter', 'exposure_time']:
        if key in data.keys() and key in obs.parameters.keys():
            if data[key] != obs.parameters[key]:
                response_data = {'failure': 'Sequence parameters were modified. If this was intentional, please press the "Modify Sequence" button instead.'}
                return response_data

    obs.parameters['reminder'] = data['reminder']
    now = datetime.utcnow()
    reminder_date = (now + timedelta(days=data['reminder'])).strftime('%Y-%m-%dT%H:%M:%S')
    obs.parameters['reminder_date'] = reminder_date
    obs.save()

    cad = DynamicCadence.objects.filter(observation_group__observation_records=obs).first()
    cad.cadence_parameters['reminder_date'] = reminder_date
    cad.save()

    return {'success': 'Continued'}


def _modify_sequence(obs, user, data):
    logger.info(f'Modifying Sequence {obs.id} for target {obs.target.id}')
    
    # Cancel the current sequence
    _stop_sequence(obs, user, data)

    new_params = obs.parameters.copy()

    if not settings.TARGET_PERMISSIONS_ONLY:
        new_params['groups'] = get_groups_with_perms(obs)
    
    new_params['comment'] = ''
    new_params['ipp_value'] = data['ipp_value']
    new_params['max_airmass'] = data['max_airmass']
    new_params['cadence_frequency_days'] = data['cadence_frequency_days']
    new_params['cadence_frequency'] = data['cadence_frequency_days'] * 24

    delay = data.get('delay_start', 0.0)
    now = datetime.utcnow()
    
    new_params['reminder'] = data['reminder']
    new_params['reminder_date'] = (now + timedelta(days=delay + data['reminder'])).strftime('%Y-%m-%dT%H:%M:%S')
    
    new_params['start_user'] = user.username

    new_params['start'] = (now + timedelta(days=delay)).strftime('%Y-%m-%dT%H:%M:%S')
    new_params['end'] = (now + timedelta(days=delay + (data['cadence_frequency_days']))).strftime('%Y-%m-%dT%H:%M:%S')

    filters = ['ipp_value', 'max_airmass', 'cadence_frequency_days', 'U', 'B', 'V', 'gp', 'rp', 'ip', 'zs', 'w', 'muscat_filter', 'exposure_time']
    for f in filters:
        if f in data and data[f]:
            new_params[f] = data[f]

    facility = get_service_class(obs.facility)()
    form_class = facility.get_form(data['observation_type'])
    form = form_class(new_params)
    if not form.is_valid():
        raise Exception(f"New parameters invalid for {obs.facility}: {form.errors}")

    observation_ids = facility.submit_observation(form.observation_payload())

    new_obs_group = ObservationGroup.objects.create(name=data['name'])
    
    for lco_id in observation_ids:
        new_record = ObservationRecord.objects.create(
            target=obs.target,
            facility=obs.facility,
            parameters=form.serialize_parameters(),
            observation_id=lco_id
        )
        new_record.parameters.update({
            'start_user': user.username,
            'reminder': new_params['reminder'],
            'reminder_date': new_params['reminder_date']
        })

        new_record.save()
        
        new_obs_group.observation_records.add(new_record)

    DynamicCadence.objects.create(
        observation_group=new_obs_group,
        cadence_strategy=new_params.get('cadence_strategy', 'SnexResumeCadenceAfterFailureStrategy'),
        cadence_parameters={'cadence_frequency': new_params['cadence_frequency'], 'reminder_date': new_params['reminder_date']},
        active=True
    )
    _sync_permissions(obs, new_obs_group)

    return {'success': 'Modified'}

def _sync_permissions(old_obs, new_group):
    group_ids = GroupObjectPermission.objects.filter(
        object_pk=old_obs.id,
        content_type=ContentType.objects.get_for_model(ObservationRecord)
    ).values_list('group_id', flat=True).distinct()
    groups = Group.objects.filter(id__in=group_ids)
    for group in groups:
        assign_perm('tom_observations.view_observationgroup', group, new_group)
        # Also assign to the new records within the group
        for record in new_group.observation_records.all():
            assign_perm('tom_observations.view_observationrecord', group, record)
