from django.db import models
from tom_targets.models import BaseTarget
from custom_code.utils import _load_table, _return_session
from custom_code.target_names import MATCH_RADIUS_ARCSEC, angular_separation, ra_ranges
from sqlalchemy import func, or_
from datetime import datetime
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

class SNExTarget(BaseTarget):
    '''
    Custom target modeling from BaseTarget for SNEx2 with attributes relating to the target details not included in BaseTarget.
    '''
    redshift = models.FloatField(null=True, blank=True)
    classification = models.CharField(max_length=30, default='', null=True, blank=True)
    reference = models.CharField(max_length=200, default='', null=True, blank=True)
    reference.hidden = True
    observing_run_priority = models.FloatField(default=0, blank=True)
    observing_run_priority.hidden = True
    last_nondetection = models.CharField(max_length=200, default='', null=True, blank=True)
    last_nondetection.hidden = True
    first_detection = models.CharField(max_length=200, default='', null=True, blank=True)
    first_detection.hidden = True
    maximum = models.CharField(max_length=200, default='', null=True, blank=True)
    maximum.hidden = True
    target_description = models.CharField(max_length=500, default='', null=True, blank=True)
    target_description.hidden = True
    gwfollowupgalaxy_id = models.FloatField(null=True, blank=True)
    gwfollowupgalaxy_id.hidden = True
    pipeline_id = models.IntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "target"
        permissions = (
            ('view_target', 'View Target'),
            ('add_target', 'Add Target'),
            ('change_target', 'Change Target'),
            ('delete_target', 'Delete Target'),
        )

    def save(self, *args, **kwargs):
        created = self.pk is None
        if created and self.pipeline_id is None:
            db_session = _return_session(settings.SNEX1_DB_URL)
            Targets = _load_table('targets', db_address=settings.SNEX1_DB_URL)
            Targetnames = _load_table('targetnames', db_address=settings.SNEX1_DB_URL)
            
            try:
                # Check if target already exists in pipeline db by coordinates
                existing = None
                if self.ra is not None and self.dec is not None:
                    r = MATCH_RADIUS_ARCSEC / 3600
                    query = db_session.query(Targets).filter(Targets.dec0.between(self.dec - r, self.dec + r))
                    ranges = ra_ranges(self.ra, self.dec, r)
                    if ranges:
                        query = query.filter(or_(*[Targets.ra0.between(lo, hi) for lo, hi in ranges]))
                    existing = next((t for t in query.all()
                                     if angular_separation(self.ra, self.dec, t.ra0, t.dec0) <= r), None)
                if not existing:
                    existing_name = db_session.query(Targetnames).filter(func.lower(func.trim(Targetnames.name)) == self.name.strip().lower()).first()
                    if existing_name:
                        existing = db_session.query(Targets).filter(
                            Targets.id == existing_name.targetid
                        ).first()
                if existing:
                    self.pipeline_id = existing.id
                else:
                    groupidcode = 1703768065789
                    now = datetime.now()
                    pipeline_target = Targets(ra0=self.ra, dec0=self.dec, groupidcode=groupidcode, lastmodified=now, datecreated=now)
                    db_session.add(pipeline_target)
                    db_session.flush()
                    self.pipeline_id = pipeline_target.id
                    db_session.add(Targetnames(targetid=pipeline_target.id, groupidcode=groupidcode, name=self.name, datecreated=now, lastmodified=now))
                    db_session.commit()
            except Exception as e:
                logger.error(f'Failed to create target in the pipeline database for {self.name}: {e}')
                db_session.rollback()
            finally:
                db_session.close()
        super().save(*args, **kwargs)