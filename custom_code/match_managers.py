import math

from django.db import models
from django.db.models import Q
from django.db.models.functions import Least
from django.db.models.functions.math import ACos, Cos, Pi, Radians, Sin
from tom_targets.base_models import TargetMatchManager

from custom_code.target_names import MATCH_RADIUS_ARCSEC, TNS_PREFIX_RE, ra_ranges


class SNExTargetMatchManager(TargetMatchManager):

    def simplify_name(self, name):
        return TNS_PREFIX_RE.sub('', super().simplify_name(name))

    def match_target(self, target, *args, **kwargs):
        queryset = self.match_name(target.name)
        if target.ra is not None and target.dec is not None:
            queryset = queryset | self.match_cone_search(target.ra, target.dec, MATCH_RADIUS_ARCSEC)
        return queryset.distinct()

    def find_duplicate(self, name, ra, dec, exclude_pk=None):
        by_name = self.match_name(name).exclude(pk=exclude_pk) if name else self.none()
        target = by_name.first()
        if target is not None:
            return target, 'name'
        if ra is not None and dec is not None:
            target = self.match_cone_search(ra, dec, MATCH_RADIUS_ARCSEC).exclude(pk=exclude_pk).order_by('separation').first()
            if target is not None:
                return target, 'position'
        return None

    def match_cone_search(self, ra, dec, radius):

        if ra is None or dec is None or radius is None or not -90 <= dec <= 90:
            return self.get_queryset().none()

        ra = ra % 360
        r = radius / 3600.0
        queryset = self.get_queryset().filter(dec__gte=dec - r, dec__lte=dec + r)
        ranges = ra_ranges(ra, dec, r)
        if ranges:
            ra_q = Q()
            for lo, hi in ranges:
                ra_q |= Q(ra__range=(lo, hi))
            queryset = queryset.filter(ra_q)

        separation = models.ExpressionWrapper(
            ACos(
                Least(
                    (Sin(math.radians(dec)) * Sin(Radians('dec'))) +
                    (Cos(math.radians(dec)) * Cos(Radians('dec')) * Cos(math.radians(ra) - Radians('ra'))), 1.0
                )
            ) * 180 / Pi(), models.FloatField()
        )
        return queryset.annotate(separation=separation).filter(separation__lte=r)
