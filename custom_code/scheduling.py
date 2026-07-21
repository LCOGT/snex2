from django.core.exceptions import NON_FIELD_ERRORS
from django.db import transaction
from guardian.models import GroupObjectPermission
from guardian.shortcuts import assign_perm, remove_perm
from django.contrib.auth.models import Group
from django.conf import settings
from datetime import timedelta
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from django_comments.models import Comment
from tom_observations.models import ObservationRecord, ObservationGroup, DynamicCadence
from tom_observations.facility import get_service_class
from tom_observations.cadence import get_cadence_strategy
from tom_targets.models import Target

import logging

logger = logging.getLogger(__name__)

def format_form_errors(errors):
    lines = []
    for field, messages in errors.items():
        for message in messages:
            if field == NON_FIELD_ERRORS:
                lines.append(str(message))
            else:
                lines.append(f'{field}: {message}')
    return '; '.join(lines)

def get_target_permission_groups(target_id):
    target_ct = ContentType.objects.get_for_model(Target)
    target_group_ids = GroupObjectPermission.objects.filter(
        object_pk=target_id,
        content_type=target_ct
    ).values_list('group_id', flat=True).distinct()
    return Group.objects.filter(id__in=target_group_ids)

def sync_group_permissions_to_target(obs_group, records, target):
    """
    Sets the Group-level permissions on obs_group (if given) and each record in
    records to exactly match the target's current permission groups, so that
    anyone who can view the target can also view/change/delete its sequences.
    """
    if settings.TARGET_PERMISSIONS_ONLY:
        return

    target_groups = set(get_target_permission_groups(target.id))

    objects = []
    if obs_group is not None:
        objects.append((obs_group, ContentType.objects.get_for_model(ObservationGroup), 'observationgroup'))
    record_ct = ContentType.objects.get_for_model(ObservationRecord)
    for record in records:
        objects.append((record, record_ct, 'observationrecord'))

    for obj, content_type, codename_model in objects:
        current_group_ids = set(GroupObjectPermission.objects.filter(
            object_pk=obj.id,
            content_type=content_type
        ).values_list('group_id', flat=True).distinct())
        target_group_ids = set(g.id for g in target_groups)

        for group in Group.objects.filter(id__in=current_group_ids - target_group_ids):
            remove_perm(f'tom_observations.view_{codename_model}', group, obj)
            remove_perm(f'tom_observations.change_{codename_model}', group, obj)
            remove_perm(f'tom_observations.delete_{codename_model}', group, obj)

        for group in target_groups:
            assign_perm(f'tom_observations.view_{codename_model}', group, obj)
            assign_perm(f'tom_observations.change_{codename_model}', group, obj)
            assign_perm(f'tom_observations.delete_{codename_model}', group, obj)

def get_proposal_choices(facility, observation_type, current_proposal=None):
    facility_class = get_service_class(facility)()
    choices = list(facility_class.get_form(observation_type).proposal_choices(facility_class))

    if current_proposal and current_proposal not in dict(choices):
        choices = [(current_proposal, current_proposal)] + choices

    return choices

def change_obs_from_scheduling(action, obs_group, user, data):
    if action == 'stop':
        logger.info(f'User {user.username} stopping sequence for group {obs_group.id}')
        return _stop_sequence(obs_group, user, data)
    elif action == 'continue':
        logger.info(f'User {user.username} continuing sequence for group {obs_group.id}')
        return _continue_sequence(obs_group, data)
    elif action == 'modify':
        logger.info(f'User {user.username} modifying sequence for group {obs_group.id}')
        return _modify_sequence(obs_group, user, data)
    
    return {'failure': 'Invalid action'}

def _stop_sequence(obs_group, user, data):
    logger.info(f'Stopping Sequence group {obs_group.id}')
    
    canceled = cancel_observation(obs_group)
    if not canceled:
        return {'failure': 'The facility (LCO) rejected the cancellation request.'}
    
    comment = data.get('comment', '')
    if comment:
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
            logger.info(f'Comment already exists for {actual_model} {object_id}')
            
        return newcomment
    except Exception as e:
        logger.error(f'Comment save failed: {e}', exc_info=True)
        return False

def cancel_observation(obs_group):    
    if not obs_group.observation_records.exists():
        logger.error(f'No observation records found in group {obs_group.id}')
        return False
    
    first_obs = obs_group.observation_records.first()
    try:
        facility_class = get_service_class(first_obs.facility)
        facility = facility_class()
    except Exception as e:
        logger.error(f'Failed to get facility service for {first_obs.facility}: {e}', exc_info=True)
        return False
    
    non_terminal_statuses = ['PENDING', '']
    records_to_update = obs_group.observation_records.filter(status__in=non_terminal_statuses)
    
    for record in records_to_update:
        try:
            facility.update_observation_status(record.observation_id)
            record.refresh_from_db()
        except Exception as e:
            logger.error(f'Failed to update status for observation {record.id}: {e}', exc_info=True)

    pending_observations = obs_group.observation_records.filter(status='PENDING')
    logger.info(f'Found {pending_observations.count()} PENDING observation(s) in group {obs_group.id}')
    
    dynamic_cadence = None
    try:
        dynamic_cadence = DynamicCadence.objects.get(observation_group=obs_group)
        logger.info(f'Current cadence status: {dynamic_cadence.active}')
        dynamic_cadence.active = False
        dynamic_cadence.save()
        logger.info(f'Dynamic Cadence turned off')
    except DynamicCadence.DoesNotExist:
        logger.warning(f"No dynamic cadence found for group {obs_group.id}")

    all_canceled = True
    for obs_to_cancel in pending_observations:
        logger.info(f'Canceling PENDING observation {obs_to_cancel.id}')
        try:
            success = facility.cancel_observation(obs_to_cancel.observation_id)
            if not success:
                logger.error(f'Facility rejected cancel for observation {obs_to_cancel.observation_id}')
                all_canceled = False
            else:
                facility.update_observation_status(obs_to_cancel.observation_id)
                obs_to_cancel.refresh_from_db()
                logger.info(f'Observation {obs_to_cancel.id} status updated from facility to {obs_to_cancel.status}')
        except Exception as e:
            logger.error(f'Exception while canceling observation {obs_to_cancel.observation_id}: {e}', exc_info=True)
            all_canceled = False
    
    if not all_canceled:
        if dynamic_cadence is not None:
            logger.error(f'One or more cancellations failed, re-activating cadence')
            dynamic_cadence.active = True
            dynamic_cadence.save()
        return False
    
    first_obs = obs_group.observation_records.order_by('created').first()
    if first_obs:
        first_obs.parameters['sequence_end'] = timezone.now().isoformat()
        first_obs.save()

    return True

def _continue_sequence(obs_group, data):
    obs = obs_group.observation_records.filter(status='PENDING').first()
    if not obs:
        cg = obs_group.dynamiccadence_set.first()
        strategy = get_cadence_strategy(cg.cadence_strategy)(cg)
        try:
            new_observations = strategy.run()
            if not new_observations:
                logger.error(f'Cadence strategy returned no new observations for group {obs_group.id}')
                return {'failure': 'No new observation was created for this sequence'}

            obs = new_observations[0]
        except Exception as e:
            logger.error((f'Unable to run cadence_group: {cg}; strategy {strategy};'
                            f' with id {cg.id} due to error: {e}'))
            return {'failure': f'There is an error with this sequence: {e}'}
        
    
    logger.info(f'Continuing Sequence group {obs_group.id} as-is')
    
    for key in ['ipp_value', 'max_airmass', 'cadence_frequency_days', 'proposal', 'U', 'B', 'V', 'up', 'gp', 'rp', 'ip', 'zs', 'w', 'muscat_filter', 'exposure_time']:
        if key in data.keys() and key in obs.parameters.keys():
            if data[key] != obs.parameters[key]:
                return {'failure': f'Sequence parameter {key} for form: {data[key]} and observation record: {obs.parameters[key]} were modified. If this was intentional, please press the "Modify Sequence" button instead.'}

    obs.parameters['reminder'] = data['reminder']
    now = timezone.now()
    reminder_date = (now + timedelta(days=data['reminder'])).isoformat()
    obs.parameters['reminder_date'] = reminder_date
    obs.save()

    try:
        cad = DynamicCadence.objects.get(observation_group=obs_group)
        cad.cadence_parameters['reminder_date'] = reminder_date
        cad.save()
    except DynamicCadence.DoesNotExist:
        logger.error(f'No dynamic cadence found for group {obs_group.id}')
        return {'failure': 'No active cadence found for this sequence'}

    return {'success': 'Continued'}

@transaction.atomic
def _modify_sequence(obs_group, user, data):
    obs = obs_group.observation_records.order_by('created').first()
    if not obs:
        return {'failure': 'No observations found in group'}
    
    logger.info(f'Modifying Sequence group {obs_group.id} for target {obs.target.id}')
    
    result = _stop_sequence(obs_group, user, data)
    if 'failure' in result:
        return result
    
    new_params = obs.parameters.copy()
    
    new_params['comment'] = ''
    new_params['proposal'] = data['proposal']
    new_params['ipp_value'] = data['ipp_value']
    new_params['max_airmass'] = data['max_airmass']
    new_params['cadence_frequency_days'] = data['cadence_frequency_days']
    new_params['cadence_frequency'] = data['cadence_frequency_days'] * 24
    new_params['start_user'] = user.username
    
    delay = data.get('delay_start', 0)
    now = timezone.now()

    start_time = now + timedelta(days=delay)
    min_window = settings.OBS_WINDOW_MINIMUM or 24
    window_length_hours = min(new_params['cadence_frequency'], min_window)

    new_params['reminder'] = data['reminder']
    new_params['reminder_date'] = (now + timedelta(days=delay + data['reminder'])).isoformat()
    new_params['start'] = start_time.isoformat()
    new_params['end'] = (start_time + timedelta(hours=window_length_hours)).isoformat()
    new_params['delay_start'] = False
    new_params['delay_amount'] = 0
    
    # Update filters
    filters = ['U', 'B', 'V', 'gp', 'up', 'rp', 'ip', 'zs', 'w', 'muscat_filter', 'exposure_time']
    for f in filters:
        if f in data and data[f]:
            new_params[f] = data[f]
    
    try:
        facility = get_service_class(obs.facility)()
        form_class = facility.get_form(data['observation_type'])
        form = form_class(data=new_params)
        
        if not form.is_valid():
            logger.error(f"Form validation failed: {format_form_errors(form.errors)}")
            raise Exception(f"New parameters invalid for {obs.facility}: {format_form_errors(form.errors)}")
        
        observation_ids = facility.submit_observation(form.observation_payload())
        
        if not observation_ids:
            raise Exception("Facility did not return any observation IDs")
            
    except Exception as e:
        logger.error(f'Failed to submit new observation: {e}', exc_info=True)
        raise
    
    new_obs_group = ObservationGroup.objects.create(name=data['name'])
    
    for lco_id in observation_ids:
        new_record = ObservationRecord.objects.create(
            target=obs.target,
            facility=obs.facility,
            parameters=new_params,
            observation_id=lco_id
        )
        new_obs_group.observation_records.add(new_record)
        logger.info(f'New observation {new_record.id} created with LCO ID: {lco_id}')
        
        try:
            facility.update_observation_status(new_record.observation_id)
            new_record.refresh_from_db()
            logger.info(f'Status updated for observation {new_record.id}: {new_record.status}')
        except Exception as e:
            logger.error(f'Failed to update status for new observation {new_record.id}: {e}', exc_info=True)
    
    DynamicCadence.objects.create(
        observation_group=new_obs_group,
        cadence_strategy=new_params.get('cadence_strategy', 'SnexResumeCadenceAfterFailureStrategy'),
        cadence_parameters={
            'cadence_frequency': new_params['cadence_frequency'],
            'cadence_frequency_days': new_params['cadence_frequency_days'],
            'reminder_date': new_params['reminder_date']
        },
        active=True
    )
    
    try:
        sync_group_permissions_to_target(new_obs_group, new_obs_group.observation_records.all(), obs.target)
        logger.info(f'Permissions synced to target for new group {new_obs_group.id}')
    except Exception as e:
        logger.error(f'Failed to sync permissions: {e}', exc_info=True)

    return {'success': 'Modified'}
