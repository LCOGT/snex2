from guardian.shortcuts import assign_perm
from django.contrib.auth.models import Group


def powers_of_two(num):
    powers = []
    i = 1
    while i <= num:
        if i & num:
            powers.append(i)
        i <<= 1
    return powers


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
