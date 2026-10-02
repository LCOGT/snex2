from dateutil.parser import parse
from guardian.models import GroupObjectPermission
from guardian.shortcuts import assign_perm, get_objects_for_user, remove_perm
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS
from django.urls import reverse
from django.utils import timezone

import logging
import requests
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from custom_code.target_names import TNS_PREFIX_RE

logger = logging.getLogger(__name__)

OBSERVATION_FORM_PREFIXES = {('SOAR', 'SPECTRA'): 'soar',
                             ('LCO', 'IMAGING'): 'phot',
                             ('LCO', 'SPECTRA'): 'spec'}


def observation_form_prefix(facility, observation_type):
    if facility == 'SOAR':
        return 'soar'
    return OBSERVATION_FORM_PREFIXES.get((facility, observation_type), 'phot')


def bind_observation_form_htmx(form, facility, observation_type):
    prefix = observation_form_prefix(facility, observation_type)
    action = reverse('submit-lco-obs', kwargs={'facility': facility})
    form.helper.form_action = action
    form.helper.attrs = {'hx-post': action,
                         'hx-target': f'#obs-form-{prefix}',
                         'hx-swap': 'outerHTML show:top'}
    return prefix


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

def viewable_dataproducts(user, queryset):
    from tom_dataproducts.models import ReducedDatum
    direct = get_objects_for_user(user, 'tom_dataproducts.view_dataproduct', klass=queryset)
    via_datums = get_objects_for_user(user, 'tom_dataproducts.view_reduceddatum',
                                      klass=ReducedDatum.objects.filter(data_product__in=queryset))
    return queryset.filter(Q(pk__in=direct.values('pk')) | Q(pk__in=via_datums.values('data_product_id')))


def dataproduct_view_groups(dp):
    from tom_dataproducts.models import ReducedDatum
    datum_pks = [str(pk) for pk in dp.reduceddatum_set.values_list('pk', flat=True)]
    perms = GroupObjectPermission.objects.filter(
        Q(content_type=ContentType.objects.get_for_model(dp), object_pk=str(dp.pk),
          permission__codename='view_dataproduct') |
        Q(content_type=ContentType.objects.get_for_model(ReducedDatum), object_pk__in=datum_pks,
          permission__codename='view_reduceddatum'))
    return set(perms.values_list('group__name', flat=True))


def _set_view_groups(codename, queryset, groups):
    for group in groups:
        assign_perm(f'tom_dataproducts.{codename}', group, queryset)
    current = GroupObjectPermission.objects.filter(
        content_type=ContentType.objects.get_for_model(queryset.model), permission__codename=codename,
        object_pk__in=[str(pk) for pk in queryset.values_list('pk', flat=True)]).values_list('group', flat=True)
    for group in Group.objects.filter(pk__in=current).exclude(pk__in=[group.pk for group in groups]):
        remove_perm(f'tom_dataproducts.{codename}', group, queryset)


def set_dataproduct_view_groups(dp, groups):
    _set_view_groups('view_dataproduct', type(dp).objects.filter(pk=dp.pk), groups)
    _set_view_groups('view_reduceddatum', dp.reduceddatum_set.all(), groups)


def set_reduceddatum_view_groups(datums, groups):
    from tom_dataproducts.models import DataProduct
    for dp in DataProduct.objects.filter(pk__in=datums.exclude(data_product=None).values('data_product')):
        set_dataproduct_view_groups(dp, groups)
    _set_view_groups('view_reduceddatum', datums.filter(data_product=None), groups)


def reduceddatum_view_groups(datums):
    perms = GroupObjectPermission.objects.filter(
        content_type=ContentType.objects.get_for_model(datums.model), permission__codename='view_reduceddatum',
        object_pk__in=[str(pk) for pk in datums.values_list('pk', flat=True)]).values_list('object_pk', 'group__name')
    visible = {}
    for pk, name in perms:
        visible.setdefault(int(pk), []).append(name)
    return {pk: sorted(names) for pk, names in visible.items()}


def groups_from_payload(groups):
    found = []
    for group in groups:
        lookup = {'pk': group['id']} if group.get('id') else {'name': group.get('name')}
        try:
            found.append(Group.objects.get(**lookup))
        except Group.DoesNotExist:
            raise ValidationError({'groups': f'Group {group} does not exist.'})
    return found


def measured(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if value >= 9999 else value


def unsubtracted_q():
    return Q(value__background_subtracted=False) | ~Q(value__has_key='background_subtracted')


def download_archive_frame(basename):
    response = requests.get(settings.FACILITIES['LCO']['archive_url'],
                            headers={'Authorization': f"Token {settings.FACILITIES['LCO']['api_key']}"},
                            params={'basename_exact': basename, 'include_related_frames': False})
    if not response.ok:
        logger.error(f'LCO archive lookup for {basename} failed: {response.status_code} {response.text[:200]}')
        return None
    results = response.json().get('results', [])
    if not results:
        return None
    return results[0]['filename'], requests.get(results[0]['url']).content


def _normalize_view_object_name(name: str) -> str:
    """
    Normalize likely target short names into a canonical compact form without spaces.

    Rules:
      - `AT` / `SN` prefix is always uppercase.
      - If the suffix is exactly 1 letter (e.g. `1993J`), that letter is uppercase.
      - If the suffix is multiple letters (e.g. `1993ab` or `2024ggi`), all letters are lowercase.

    Examples:
      - `2024ggi` -> `AT2024ggi` (default AT when no SN/AT prefix is provided)
      - `SN2024ggi` -> `SN2024ggi` (preserve explicit SN)
      - `AT1993J` -> `AT1993J`
      - `2024ab` -> `AT2024ab`
    """
    s_clean = (name or '').strip().replace(' ', '')
    if not s_clean:
        return s_clean

    if TNS_PREFIX_RE.match(s_clean):
        prefix = s_clean[:2].upper()
        tail = s_clean[2:]
    elif s_clean[0].isdigit():
        prefix = 'AT'
        tail = s_clean
    else:
        return s_clean

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
        return s_clean
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
    if TNS_PREFIX_RE.match(s):
        return s[:2].upper() + ' ' + s[2:]
    return s


GENERATED_ASCII_PREFIX = 'spectrum-'


def spectrum_ascii(rd):
    if not rd or not isinstance(rd.value, dict):
        return None
    if rd.value.get('photon_flux'):
        wavelength, flux = rd.value.get('wavelength'), rd.value['photon_flux']
    elif rd.value.get('flux'):
        wavelength, flux = rd.value.get('wavelength'), rd.value['flux']
    else:
        points = [point for point in rd.value.values() if isinstance(point, dict) and 'wavelength' in point and 'flux' in point]
        wavelength, flux = [point['wavelength'] for point in points], [point['flux'] for point in points]
    if not wavelength or not flux or len(wavelength) != len(flux):
        return None
    lines = [f'{w} {f}' for w, f in zip(wavelength, flux)]
    return ('\n'.join(lines)).encode('utf-8')


def spectrum_ascii_name(datum):
    return '{}_{}.ascii'.format(datum.target.name.replace(' ', '_'), datum.timestamp.strftime('%Y%m%dT%H%M%S'))
