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
