from django.db import transaction
from tom_targets.models import TargetList, Target, TargetName
from guardian.models import GroupObjectPermission
from guardian.shortcuts import get_objects_for_user, assign_perm, remove_perm, get_users_with_perms
from django.contrib.auth.models import User, Group
from django.conf import settings
from datetime import datetime, date, timedelta
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect, FileResponse
from django.contrib.contenttypes.models import ContentType
import json
from django_comments.models import Comment
from tom_common.hooks import run_hook
from tom_observations.facilities.lco import LCOSettings
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
    data: cleaned data from PhotSchedulingForm
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
    logger.info('Stopping Sequence')
    
    ## Cancel observation request in LCO portal
    canceled = cancel_observation(obs)
    if not canceled:
        raise Exception("The facility (LCO) rejected the cancellation request.")
    
    obs_group = obs.observationgroup_set.first()
    comment = data.get('comment', {})

    if comment and obs_group:
        save_comments(comment, obs_group.id, user)
                
    return "Stopped"

def save_comments(comment_text, object_id, user, model_name='observationgroup'):
    try:
        model_map = {
            'observationgroup': 'observationgroup',
            'spec': 'reduceddatum',
            'targets': 'snextarget'
        }
        actual_model = model_map.get(model_name, model_name)
        content_type = ContentType.objects.get(model=actual_model)
        newcomment = Comment.objects.create(
            object_pk=str(object_id),
            content_type=content_type,
            user=user,
            user_name=user.username,
            user_email=user.email,
            comment=comment_text,
            submit_date=datetime.now(),
            is_public=True,
            is_removed=False,
            site_id=getattr(settings, 'SITE_ID', 1)
        )
        newcomment.save()
        return newcomment
    except Exception as e:
        logger.error(f"Comment save failed: {e}")        
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
    logger.info(f'Continuing Sequence {obs.id} as-is')
    
    for key in ['ipp_value', 'max_airmass', 'cadence_frequency']:

        if data.get(key) != obs.parameters.get(key):
            raise Exception("Parameters were modified. Please use 'Modify' instead.")

    params = obs.parameters
    params['reminder'] = data['reminder']
    now = datetime.utcnow()
    params['reminder_date'] = (now + timedelta(days=data['reminder'])).strftime('%Y-%m-%dT%H:%M:%S')
    
    obs.parameters = params
    obs.save()
    
    return "Continued"


def _modify_sequence(obs, user, data):
    logger.info(f'Modifying Sequence {obs.id} for target {obs.target.id}')
    
    # Cancel the current sequence
    _stop_sequence(obs, user, data)

    old_params = obs.parameters
    logger.info(f'old parameters {old_params}')
    logger.info(f'incoming data {data}')
    new_params = obs.parameters.copy()

    new_params['ipp_value'] = data['ipp_value']
    new_params['max_airmass'] = data['max_airmass']
    new_params['cadence_frequency'] = data['cadence_frequency']

    delay = data.get('delay_start', 0.0)
    now = datetime.utcnow()
    
    new_params['reminder'] = data['reminder']
    new_params['reminder_date'] = (now + timedelta(days=delay + data['reminder'])).strftime('%Y-%m-%dT%H:%M:%S')
    
    new_params['start_user'] = user.username

    new_params['start'] = (now + timedelta(days=delay)).strftime('%Y-%m-%dT%H:%M:%S')
    new_params['end'] = (now + timedelta(days=delay + (data['cadence_frequency']))).strftime('%Y-%m-%dT%H:%M:%S')

    filters = ['U', 'B', 'V', 'R', 'I', 'up', 'gp', 'rp', 'ip', 'zs', 'w']
    for f in filters:
        if f in data and data[f]:
            new_params[f] = data[f]

    facility_class = get_service_class(obs.facility)
    facility = facility_class()
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
            'reminder': data['reminder'],
            'reminder_date': new_params['reminder_date']
        })

        new_record.save()
        
        new_obs_group.observation_records.add(new_record)

    DynamicCadence.objects.create(
        observation_group=new_obs_group,
        cadence_strategy=data.get('cadence_strategy', 'SnexResumeCadenceAfterFailureStrategy'),
        cadence_parameters={'cadence_frequency': data['cadence_frequency']},
        active=True
    )

    _sync_permissions(obs, new_obs_group)

    return "Modified"

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
