from sqlalchemy import create_engine, pool
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.automap import automap_base
from contextlib import contextmanager

from dateutil.parser import parse
from guardian.models import GroupObjectPermission
from guardian.shortcuts import assign_perm, remove_perm
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS
from django.utils import timezone

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

def apply_proposal_rollover(observation_payload, start_keyword='start'):
    start = None
    for rollover in getattr(settings, 'PROPOSAL_ROLLOVERS', []):
        if observation_payload.get('proposal') != rollover['old_id']:
            continue
        if start is None:
            start_value = observation_payload.get(start_keyword)
            if not start_value:
                return observation_payload
            start = parse(start_value) if isinstance(start_value, str) else start_value
            if timezone.is_naive(start):
                start = timezone.make_aware(start)
        semester_start = parse(rollover['semester_start'])
        if timezone.is_naive(semester_start):
            semester_start = timezone.make_aware(semester_start)
        if start >= semester_start:
            logger.info(f"Rolling over proposal {rollover['old_id']} to {rollover['new_id']} for window starting {observation_payload.get(start_keyword)}")
            observation_payload['proposal'] = rollover['new_id']
    return observation_payload

def powers_of_two(num):
    powers = []
    i = 1
    while i <= num:
        if i & num:
            powers.append(i)
        i <<= 1
    return powers

@contextmanager
def _get_session(db_address):
    Base = automap_base()
    engine = create_engine(db_address, poolclass=pool.NullPool)
    Base.metadata.bind = engine

    db_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = db_session()

    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()

def update_permissions(groupid, permission, obj, snex1_groups):
    """
    Updates permissions of a specific group for a certain target
    or reduceddatum

    Parameters
    ----------
    groupid: int, corresponding to which groups in SNex1 have permissions for this object
    permissionid: int, the permission name in the SNex2 db for this permission
    obj: Django model instance, e.g. Target or ReducedDatum
    snex1_groups: list of groups from snex1 to assign permissions with
    """

    target_groups = powers_of_two(groupid)

    for g_name, g_id in snex1_groups.items():
        if g_id in target_groups:
            snex2_group = Group.objects.filter(name=g_name).first()
            assign_perm(permission, snex2_group, obj)

TARGET_CONTENT_TYPES = (('custom_code', 'snextarget'), ('tom_targets', 'target'))

def get_target_permission_groups(target_id):
    best = Group.objects.none()
    for app_label, model in TARGET_CONTENT_TYPES:
        content_type = ContentType.objects.filter(app_label=app_label, model=model).first()
        if not content_type:
            continue
        group_ids = GroupObjectPermission.objects.filter(
            object_pk=str(target_id),
            content_type=content_type
        ).values_list('group_id', flat=True).distinct()
        groups = Group.objects.filter(id__in=group_ids)
        if groups.count() > best.count():
            best = groups
    return best

def sync_group_permissions_to_target(obs_group, records, target):
    if settings.TARGET_PERMISSIONS_ONLY:
        return

    target_groups = set(get_target_permission_groups(target.id))
    target_group_ids = set(g.id for g in target_groups)

    objects = []
    if obs_group is not None:
        group_ct = ContentType.objects.get(app_label='tom_observations', model='observationgroup')
        objects.append((obs_group, group_ct, 'observationgroup'))
    record_ct = ContentType.objects.get(app_label='tom_observations', model='observationrecord')
    for record in records:
        objects.append((record, record_ct, 'observationrecord'))

    for obj, content_type, codename_model in objects:
        current_group_ids = set(GroupObjectPermission.objects.filter(
            object_pk=str(obj.id),
            content_type=content_type
        ).values_list('group_id', flat=True).distinct())

        for group in Group.objects.filter(id__in=current_group_ids - target_group_ids):
            remove_perm(f'tom_observations.view_{codename_model}', group, obj)
            remove_perm(f'tom_observations.change_{codename_model}', group, obj)
            remove_perm(f'tom_observations.delete_{codename_model}', group, obj)

        for group in target_groups:
            assign_perm(f'tom_observations.view_{codename_model}', group, obj)
            assign_perm(f'tom_observations.change_{codename_model}', group, obj)
            assign_perm(f'tom_observations.delete_{codename_model}', group, obj)

def _normalize_view_object_name(name: str) -> str:
    """
    Normalize likely target short names into a canonical compact form without spaces.

    Rules:
      - `AT` / `SN` prefix is always uppercase.
      - If the suffix is exactly 1 letter (e.g. `1993J`), that letter is uppercase.
      - If the suffix is multiple letters (e.g. `1993ab` or `24ggi`), all letters are lowercase.

    Examples:
      - `24ggi` -> `AT2024ggi` (default AT when no SN/AT prefix is provided)
      - `SN2024ggi` -> `SN2024ggi` (preserve explicit SN)
      - `AT1993J` -> `AT1993J`
      - `2024ab` -> `AT2024ab`
    """
    s_clean = (name or '').strip().replace(' ', '')
    if not s_clean:
        return s_clean

    s_upper = s_clean.upper()
    if s_upper.startswith('SN'):
        prefix = 'SN'
        tail = s_clean[2:]
    elif s_upper.startswith('AT'):
        prefix = 'AT'
        tail = s_clean[2:]
    else:
        prefix = 'AT'
        tail = s_clean

    # Find the first alphabetic character in `tail`; digits before that are the year.
    first_alpha_idx = None
    for i, ch in enumerate(tail):
        if ch.isalpha():
            first_alpha_idx = i
            break

    if first_alpha_idx in (None, 0):
        # Can't parse year; at least ensure prefix casing.
        return prefix + tail

    year_part = tail[:first_alpha_idx]
    suffix_raw = tail[first_alpha_idx:]

    if not year_part.isdigit():
        return prefix + year_part + suffix_raw

    if len(year_part) == 2:
        year_full = 2000 + int(year_part)
    elif len(year_part) == 4:
        year_full = int(year_part)
    else:
        # Unknown year length; preserve raw year.
        year_full = year_part

    # Apply suffix letter casing rule.
    # We only look at the initial contiguous letter run.
    import re
    m = re.match(r'([A-Za-z]+)', suffix_raw)
    letters = m.group(1) if m else ''
    rest = suffix_raw[len(letters):] if letters else suffix_raw

    if len(letters) == 1:
        letters_cased = letters.upper()
    else:
        letters_cased = letters.lower()

    return f"{prefix}{year_full}{letters_cased}{rest}"


def _format_prefixed_name_for_create(canonical_name: str) -> str:
    """
    Format canonical name for the create form display, e.g.:
      - `SN2024GGI` -> `SN 2024GGI`
      - `AT2024GGI` -> `AT 2024GGI`
    """
    s = (canonical_name or '').strip()
    s_upper = s.upper()
    if s_upper.startswith('SN'):
        return 'SN ' + s[2:]
    if s_upper.startswith('AT'):
        return 'AT ' + s[2:]
    return s

def _return_session(db_address=settings.SNEX1_DB_URL):
    ### This one is not run within a with loop, must be closed manually
    Base = automap_base()
    engine = create_engine(db_address, poolclass=pool.NullPool)
    Base.metadata.bind = engine

    db_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = db_session()

    return session

def _load_table(tablename, db_address):
    Base = automap_base()
    engine = create_engine(db_address, poolclass=pool.NullPool)
    Base.prepare(engine, reflect=True)

    table = getattr(Base.classes, tablename)
    return(table)
