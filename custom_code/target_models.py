from django.db import models
from tom_targets.models import BaseTarget
from django.contrib.auth.models import User

STATUS_CHOICES = (
    ('in prep', 'In Prep'),
    ('submitted', 'Submitted'),
    ('published', 'Published')
)

class SNExTarget(BaseTarget):
    '''
    Custom target modeling from BaseTarget for SNEx2 with attributes relating to the target details not included in BaseTarget.
    '''
    redshift = models.FloatField(default=0)
    classification = models.CharField(max_length=30, default='', null=True, blank=True)
    reference = models.CharField(max_length=200, default='', null=True, blank=True)
    reference.hidden = True
    observing_run_priority = models.FloatField(default=0)
    observing_run_priority.hidden = True
    last_nondetection = models.CharField(max_length=200,default='',null=True,blank=True)
    last_nondetection.hidden = True
    first_detection = models.CharField(max_length=200,default='',null=True,blank=True)
    first_detection.hidden = True
    maximum = models.CharField(max_length=200,default='',null=True,blank=True)
    maximum.hidden = True
    target_description = models.CharField(max_length=500,default='',null=True,blank=True)
    target_description.hidden = True
    gwfollowupgalaxy_id = models.FloatField(null=True,blank=True)
    gwfollowupgalaxy_id.hidden = True
    
    class Meta:
        verbose_name = "target"
        permissions = (
            ('view_target', 'View Target'),
            ('add_target', 'Add Target'),
            ('change_target', 'Change Target'),
            ('delete_target', 'Delete Target'),
        )