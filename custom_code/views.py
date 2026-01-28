from django_filters.views import FilterView
from django.shortcuts import redirect, render
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q, DateTimeField, FloatField, F, ExpressionWrapper, OuterRef, Subquery, Count, Exists, Count, CharField, Value, Max, Sum
from django.db.models.functions import Cast
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect, FileResponse
from django.views.generic.base import TemplateView, RedirectView
from django.views.generic.list import ListView
from django.views.generic.edit import FormView
from django.views.generic.detail import DetailView
from django.urls import reverse, reverse_lazy
from django.template.loader import render_to_string
from django.template.context import RequestContext
from django_comments.models import Comment
from django_comments.signals import comment_was_posted
from django.dispatch import receiver

from tom_targets.models import TargetList, Target, TargetName
from custom_code.models import TNSTarget, ScienceTags, TargetTags, ReducedDatumExtra, Papers, InterestedPersons, BrokerTarget
from custom_code.filters import TNSTargetFilter, CustomTargetFilter, BrokerTargetFilter, BrokerTargetForm
from custom_code.forms import SNEx2UserCreationForm
from guardian.mixins import PermissionListMixin
from guardian.models import GroupObjectPermission
from guardian.shortcuts import get_objects_for_user, assign_perm, remove_perm, get_users_with_perms
from django.contrib.auth.models import User, Group
from django.contrib.contenttypes.models import ContentType
from django.contrib import messages
from django.conf import settings
from django.db.models.fields.json import KeyTextTransform

from astropy.coordinates import SkyCoord
from astropy import units as u
from astropy.time import Time
import time
from datetime import datetime, date, timedelta
import json
from io import StringIO
import random

import plotly.graph_objs as go
from tom_dataproducts.models import ReducedDatum, DataProduct
from custom_code.templatetags import custom_code_tags
from tom_observations.templatetags.observation_extras import observing_buttons
from tom_dataproducts.templatetags.dataproduct_extras import dataproduct_list_for_target
from tom_targets.templatetags.targets_extras import target_groups
from custom_code.hooks import _get_tns_params, _return_session, get_unreduced_spectra, get_standards_from_snex1
from custom_code.thumbnails import make_thumb

from .forms import CustomTargetCreateForm, CustomDataProductUploadForm, PapersForm, ReferenceStatusForm,ThumbnailForm
from tom_targets.views import TargetCreateView
from tom_common.hooks import run_hook

from tom_common.views import UserUpdateView
from tom_dataproducts.views import DataProductUploadView, DataProductDeleteView
from tom_dataproducts.exceptions import InvalidFileFormatException
from tom_targets.base_models import TargetMatchManager

from custom_code.processors.data_processor import run_custom_data_processor
from guardian.shortcuts import assign_perm

from tom_observations.models import ObservationRecord, ObservationGroup, DynamicCadence
from tom_observations.facility import get_service_class
from tom_observations.cadence import get_cadence_strategy
from tom_observations.facilities.lco import LCOSettings
from tom_observations.views import ObservationCreateView, ObservationListView
from tom_registration.registration_flows.approval_required.views import UserApprovalView
import base64
import requests
import django_filters
from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit, Layout, Div, HTML, Fieldset, Row, Column
from crispy_forms.bootstrap import PrependedAppendedText, PrependedText
from django.utils import timezone
import logging
from io import BytesIO
import os

from tom_targets.models import Target

logger = logging.getLogger(__name__)

## debug
logger.setLevel(logging.DEBUG) 

# Create your views here.

def make_coords(ra, dec):
    coords = SkyCoord(ra, dec, unit=u.deg)
    coords = coords.to_string('hmsdms',sep=':',precision=1,alwayssign=True)
    return coords

def make_lnd(mag, filt, jd, jd_now):
    if not jd:
        return 'Archival'
    diff = jd_now - jd
    lnd = '{mag:.2f} ({filt}: {time:.2f})'.format(
        mag = mag,
        filt = filt,
        time = diff)
    return lnd

def make_magrecent(all_phot, jd_now):
    all_phot = json.loads(all_phot)
    jds = [all_phot[obs]['jd'] for obs in all_phot]
    if not jds:
        return 'None'
    recent_jd = max(jds)
    recent_phot = [all_phot[obs] for obs in all_phot if
        all_phot[obs]['jd'] == recent_jd][0]
    mag = float(recent_phot['flux'])
    filt = recent_phot['filters']['name']
    diff = jd_now - float(recent_jd)
    mag_recent = '{mag:.2f} ({filt}: {time:.2f})'.format(
        mag = mag,
        filt = filt,
        time = diff)
    return mag_recent

class TNSTargets(FilterView):

    # Look at https://simpleisbetterthancomplex.com/tutorial/2016/11/28/how-to-filter-querysets-dynamically.html
    
    template_name = 'custom_code/tns_targets.html'
    model = TNSTarget
    paginate_by = 10
    context_object_name = 'tnstargets'
    strict = False
    filterset_class = TNSTargetFilter

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        jd_now = Time(datetime.utcnow()).jd
        TNS_URL = "https://www.wis-tns.org/object/"
        for target in context['object_list']:
            logger.info('Getting context data for TNS Target %s', target)
            target.coords = make_coords(target.ra, target.dec)
            target.mag_lnd = make_lnd(target.lnd_maglim,
                target.lnd_filter, target.lnd_jd, jd_now)
            target.mag_recent = make_magrecent(target.all_phot, jd_now)
            target.link = TNS_URL + target.name
        return context

class TargetListView(PermissionListMixin, FilterView):
    """
    View for listing targets in the TOM. Only shows targets that the user is authorized to view.     Requires authorization.
    """
    template_name = 'tom_targets/target_list.html'
    paginate_by = 25
    strict = False
    model = Target
    filterset_class = CustomTargetFilter
    permission_required = 'tom_targets.view_target'
    ordering = ['-id']

    def get_context_data(self, *args, **kwargs):
        """
        Adds the number of targets visible, the available ``TargetList`` objects if the user is a    uthenticated, and
        the query string to the context object.

        :returns: context dictionary
        :rtype: dict
        """
        context = super().get_context_data(*args, **kwargs)
        context['target_count'] = context['paginator'].count
        # hide target grouping list if user not logged in
        context['groupings'] = (TargetList.objects.all()
                                if self.request.user.is_authenticated
                                else TargetList.objects.none())
        context['query_string'] = self.request.META['QUERY_STRING']
        return context

def target_redirect_view(request):
 
    search_entry = request.GET['name'] 
    logger.info('Redirecting search for %s', search_entry)

    target_search_coords = None
    if ':' in search_entry or '.' in search_entry:
        search_entry = search_entry.replace(',', ' ')
        target_search_coords = search_entry.split()

    if target_search_coords is not None:
        ra = target_search_coords[0]
        dec = target_search_coords[1]
        radius = 1.0/60.0 #1 arcmin search radius

        if ':' in ra and ':' in dec:
            ra_hms = ra.split(':')
            ra_hour = float(ra_hms[0])
            ra_min = float(ra_hms[1])
            ra_sec = float(ra_hms[2])

            dec_dms = dec.split(':')
            dec_deg = float(dec_dms[0])
            dec_min = float(dec_dms[1])
            dec_sec = float(dec_dms[2])

            # Convert to degree
            ra = (ra_hour*15) + (ra_min*15/60) + (ra_sec*15/3600)
            if dec_deg > 0:
                dec = dec_deg + (dec_min/60) + (dec_sec/3600)
            else:
                dec = dec_deg - (dec_min/60) - (dec_sec/3600)

        else:
            ra = float(ra)
            dec = float(dec)

        target_match_list = Target.objects.filter(ra__gte=ra-radius, ra__lte=ra+radius, dec__gte=dec-radius, dec__lte=dec+radius)

        if len(target_match_list) == 1:
            target_id = target_match_list[0].id
            return(redirect('/targets/{}/'.format(target_id)))
        
        elif len(target_match_list) > 1:
            return(redirect('/targets/?cone_search={ra}%2C{dec}%2C{radius}'.format(ra=ra,dec=dec,radius=radius)))
        else:
            return(redirect('/create-target/?ra={ra}&dec={dec}'.format(ra=ra,dec=dec)))

    else:
        target_match_list = Target.objects.filter(Q(name__icontains=search_entry) | Q(aliases__name__icontains=search_entry) | Q(name__icontains=search_entry.lower().replace('SN ','')) | Q(aliases__name__icontains=search_entry.lower().replace('AT ',''))).distinct()

        if len(target_match_list) == 1:
            target_id = target_match_list[0].id
            return(redirect('/targets/{}/'.format(target_id)))

        elif len(target_match_list) > 1: 
            return(redirect('/targets/?name={}'.format(search_entry)))
        else:
            return(redirect('/create-target/?name={name}'.format(name=search_entry)))


def add_tag_view(request):
    new_tag = request.GET.get('new_tag', None)
    username = request.user.username
    tag, _ = ScienceTags.objects.get_or_create(tag=new_tag, userid=username)
    response_data = {'success': 1}
    return HttpResponse(json.dumps(response_data), content_type='application/json')


def save_target_tag_view(request):
    tag_names = json.loads(request.GET.get('tags', None))
    target_id = request.GET.get('targetid', None)
    TargetTags.objects.all().filter(target_id=target_id).delete()
    for i in range(len(tag_names)):
        tag_id = ScienceTags.objects.filter(tag=tag_names[i]).first().id
        target_tag, _ = TargetTags.objects.get_or_create(tag_id=tag_id, target_id=target_id)
    response_data = {'success': 1}
    return HttpResponse(json.dumps(response_data), content_type='application/json')


def targetlist_collapse_view(request):

    target_id = request.GET.get('target_id', None)
    logger.info('Getting plots for target %s', target_id)
    target = Target.objects.get(id=target_id)
    user_id = request.GET.get('user_id', None)
    user = User.objects.get(id=user_id)

    lightcurve_plot = custom_code_tags.lightcurve_collapse(target, user)['plot']
    spectra_plot = custom_code_tags.spectra_collapse(target, user)['plot']
    airmass_plot = custom_code_tags.airmass_collapse(target)['figure']

    context = {
        'lightcurve_plot': lightcurve_plot,
        'spectra_plot': spectra_plot,
        'airmass_plot': airmass_plot
    }

    return HttpResponse(json.dumps(context), content_type='application/json')

class CustomTargetCreateView(TargetCreateView):

    def get_form_class(self):
        return CustomTargetCreateForm

    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        form.fields['groups'].queryset = Group.objects.all()
        return form

    def get_initial(self):
        return {
            'type': self.get_target_type(),
            'groups': Group.objects.filter(name__in=settings.DEFAULT_GROUPS),
            **dict(self.request.GET.items())
        }

    def get_context_data(self, **kwargs):
        context = super(CustomTargetCreateView, self).get_context_data(**kwargs)
        context['type_choices'] = Target.TARGET_TYPES
        return context

    def post(self, request):
        super(CustomTargetCreateView, self).post(request)
        return redirect(self.get_success_url())
    
    def form_valid(self, form):

        # First, create the targets in both dbs and nothing else
        with transaction.atomic():
            if form.is_valid():

                ### Check that there are no targets with the same name or coordinates
                target_cone_search = Target.objects.filter(ra__gte=form.cleaned_data['ra']-4.0/3600.0, ra__lte=form.cleaned_data['ra']+4.0/3600.0, dec__gte=form.cleaned_data['dec']-4.0/3600.0, dec__lte=form.cleaned_data['dec']+4.0/3600.0)
                if target_cone_search.count() > 0.0:
                    logger.info('There exists another target near the coordinates {} {}'.format(form.cleaned_data['ra'], form.cleaned_data['dec']))
                    form.errors['ra'] = ['Target found with same or similar coordinates']
                    form.errors['dec'] = ['Target found with same or similar coordinates']
                    return super().form_invalid(form)

                name_lookup = form.cleaned_data['name'].replace('SN', '').replace('AT', '').replace(' ', '')
                target_name_search = Target.objects.filter(Q(name__icontains=name_lookup) | Q(aliases__name__icontains=name_lookup)).distinct()

                if target_name_search.count() > 0.0:
                    logger.info('Target with name {} already exists'.format(form.cleaned_data['name']))
                    form.errors['name'] = ['Target found with same name']
                    return super().form_invalid(form)


                groups = [g.name for g in form.cleaned_data['groups']]
                self.object = form.save(form)

                # Sync with SNEx1
                db_session = _return_session()
                run_hook('target_post_save', target=self.object, created=True, group_names=groups, wrapped_session=db_session)
                db_session.commit()
            else:
                logger.info('Submitting target failed with errors {}'.format(form.errors))
                return super().form_invalid(form)

        # If that works, ingest extra stuff for SNEx2 target only
        # Run in separate atomic transaction block to avoid rolling back
        # target creation if extra data ingestion goes wrong
        with transaction.atomic():
            run_hook('target_post_save', target=self.object, created=False)

        return redirect(self.get_success_url())

    def get_initial(self):
        return {
            'type': self.get_target_type(),
            'groups': Group.objects.filter(name__in=settings.DEFAULT_GROUPS),
            **dict(self.request.GET.items())
        }
    

class CustomUserUpdateView(UserUpdateView):

    form_class = SNEx2UserCreationForm

    def get_success_url(self):
        """
        Returns the redirect URL for a successful update. If the current user is a superuser, returns the URL for the
        user list. Otherwise, returns the URL for updating the current user.

        :returns: URL for user list or update user
        :rtype: str
        """
        if self.request.user.is_superuser:
            return reverse_lazy('user-list')
        else:
            return reverse_lazy('custom_code:custom-user-update', kwargs={'pk': self.request.user.id})

    def dispatch(self, *args, **kwargs):
        """
        Directs the class-based view to the correct method for the HTTP request method. Ensures that non-superusers
        are not incorrectly updating the profiles of other users.
        """
        if not self.request.user.is_superuser and self.request.user.id != self.kwargs['pk']:
            return redirect('custom_code:custom-user-update', self.request.user.id)
        else:
            return super().dispatch(*args, **kwargs)

    def form_valid(self, form):
        old_username = self.get_object().username
        super().form_valid(form)
        run_hook('sync_users_with_snex1', self.get_object(), False, old_username)
        return redirect(self.get_success_url())


class SNEx2UserApprovalView(UserApprovalView):

    def form_valid(self, form):
        response = super().form_valid(form)
        run_hook("sync_users_with_snex1", self.get_object(), True)

        return response


class CustomDataProductUploadView(DataProductUploadView):

    form_class = CustomDataProductUploadForm

    def form_valid(self, form):

        target = form.cleaned_data['target']
        if not target:
            observation_record = form.cleaned_data['observation_record']
            target = observation_record.target
        else:
            observation_record = None
        dp_type = form.cleaned_data['data_product_type']
        data_product_files = self.request.FILES.getlist('files')
        successful_uploads = []
        for f in data_product_files:
            dp = DataProduct(
                target=target,
                observation_record=observation_record,
                data=f,
                product_id=None,
                data_product_type=dp_type
            )
            dp.save()
            try:
                #run_hook('data_product_post_upload', dp)

                ### ------------------------------------------------------------------
                ### Create row in ReducedDatumExtras with the extra info
                rdextra_value = {'data_product_id': int(dp.id)}
                if dp_type == 'photometry':
                    extras = {'reduction_type': 'manual'}
                    rdextra_value['photometry_type'] = form.cleaned_data['photometry_type']
                    background_subtracted = form.cleaned_data['background_subtracted']
                    if background_subtracted:
                        extras['background_subtracted'] = True
                        extras['subtraction_algorithm'] = form.cleaned_data['subtraction_algorithm']
                        extras['template_source'] = form.cleaned_data['template_source']

                else: #Don't need to append anything to reduceddatum value if not photometry
                    extras = {}
                    rdextra_value['telescope'] = form.cleaned_data['telescope']
                    rdextra_value['exptime'] = form.cleaned_data['exposure_time']
                    rdextra_value['slit'] = form.cleaned_data['slit']
                    rdextra_value['date_obs'] = form.cleaned_data['date_obs']
                
                rdextra_value['instrument'] = form.cleaned_data['instrument']
                reducer_group = form.cleaned_data['reducer_group']
                if dp_type == 'spectroscopy':
                    rdextra_value['reducer'] = reducer_group
                elif dp_type == 'photometry' and reducer_group != 'LCO':
                    rdextra_value['reducer_group'] = reducer_group

                used_in = form.cleaned_data['used_in']
                if used_in:
                    rdextra_value['used_in'] = int(used_in.id)
                rdextra_value['final_reduction'] = form.cleaned_data['final_reduction']
                reduced_data = run_custom_data_processor(dp, extras, rdextra_value)
                reduced_datum_extra = ReducedDatumExtra(
                    target = target,
                    data_type = dp_type,
                    key = 'upload_extras',
                    value = json.dumps(rdextra_value)
                )
                reduced_datum_extra.save()

                ### -------------------------------------------------------------------
                
                if not settings.TARGET_PERMISSIONS_ONLY:
                    for group in form.cleaned_data['groups']:
                        assign_perm('tom_dataproducts.view_dataproduct', group, dp)
                        assign_perm('tom_dataproducts.delete_dataproduct', group, dp)
                        assign_perm('tom_dataproducts.view_reduceddatum', group, reduced_data)
                successful_uploads.append(str(dp))
            except InvalidFileFormatException as iffe:
                ReducedDatum.objects.filter(data_product=dp).delete()
                dp.delete()
                messages.error(
                    self.request,
                    'File format invalid for file {0} -- error was {1}'.format(str(dp), iffe)
                )
            except Exception as e:
                ReducedDatum.objects.filter(data_product=dp).delete()
                dp.delete()
                ReducedDatumExtra.objects.filter(target=target, value=json.dumps(rdextra_value)).delete()
                messages.error(self.request, 'There was a problem processing your file: {0}'.format(str(dp)))
                print(e)
        if successful_uploads:
            messages.success(
                self.request,
                'Successfully uploaded: {0}'.format('\n'.join([p for p in successful_uploads]))
            )

        return redirect(form.cleaned_data.get('referrer', '/'))


class CustomDataProductDeleteView(DataProductDeleteView):

    def form_valid(self, request, *args, **kwargs):
        # Delete the ReducedDatumExtra row
        reduced_datum_query = ReducedDatumExtra.objects.filter(key='upload_extras')
        for row in reduced_datum_query:
            value = json.loads(row.value) 
            if value.get('data_product_id', '') == int(self.get_object().id):
                row.delete()
                break
        return self.delete(request, *args, **kwargs)


def save_dataproduct_groups_view(request):
    group_names = json.loads(request.GET.get('groups', None))
    dataproduct_id = request.GET.get('dataproductid', None)
    dp = DataProduct.objects.get(id=dataproduct_id)
    data = ReducedDatum.objects.filter(data_product=dp)
    successful_groups = ''
    for i in group_names:
        group = Group.objects.get(name=i)
        assign_perm('tom_dataproducts.view_dataproduct', group, dp)
        for datum in data:
            assign_perm('tom_dataproducts.view_reduceddatum', group, datum)
        successful_groups += i
    response_data = {'success': successful_groups}
    return HttpResponse(json.dumps(response_data), content_type='application/json')


class Snex1ConnectionError(Exception):
    def __init__(self, message="Error syncing with the SNEx1 database"):
        self.message = message
        super().__init__(self.message)


class PaperCreateView(FormView):
    
    form_class = PapersForm
    template_name = 'custom_code/papers_list.html'

    def form_valid(self, form):
        target = form.cleaned_data['target']
        first_name = form.cleaned_data['author_first_name']
        last_name = form.cleaned_data['author_last_name']
        status = form.cleaned_data['status']
        description = form.cleaned_data['description']
        paper = Papers(
                target=target,
                author_first_name=first_name,
                author_last_name=last_name,
                status=status,
                description=description
            )
        paper.save()
        run_hook('sync_paper_with_snex1', paper)
        
        return HttpResponseRedirect('/targets/{}/'.format(target.id))


def save_comments(comment, object_id, user, tablename='observationgroup'):

    try:
        if tablename == 'observationgroup':
            content_type_id = ContentType.objects.get(model='observationgroup').id
        else:
            tablename_dict = {'spec': 'reduceddatum',
                              'targets': 'snextarget'}
            snex2_model = tablename_dict[tablename]
            content_type_id = ContentType.objects.get(model=snex2_model).id

        newcomment = Comment(
            object_pk=object_id,
            user_name=user.username,
            user_email=user.email,
            comment=comment,
            submit_date=datetime.strftime(datetime.now(), '%Y-%m-%d %H:%M:%S'),
            is_public=True,
            is_removed=False,
            content_type_id=content_type_id,
            site_id=2,
            user_id=user.id
        )
        newcomment.save()
        return True
    except:
        return False


def save_comments_view(request):
    comment = request.GET['comment']
    object_id = int(request.GET['object_id'])
    user_id = int(request.GET['user_id'])
    tablename = request.GET['tablename']

    user = User.objects.get(id=user_id)

    saved = save_comments(comment, object_id, user, tablename=tablename)

    if saved:
        if tablename == 'spec':
            ### Save comment in SNEx1 as well
            spec = ReducedDatum.objects.get(id=object_id)
            target_id = int(spec.target_id)
            snex_id_row = ReducedDatumExtra.objects.filter(data_type='spectroscopy', key='snex_id', target_id=target_id, value__icontains='"snex2_id": {}'.format(object_id)).first()
            if snex_id_row:
                snex1_id = json.loads(snex_id_row.value)['snex_id']
                run_hook('sync_comment_with_snex1', comment, 'spec', user_id, target_id, snex1_id)
        
        return HttpResponse(json.dumps({'success': 'Saved'}))
    
    else:
        return HttpResponse(json.dumps({'failure': 'Failed to save'}))


def cancel_observation(obs):
     
    # Get the last non-template ObservationRecord and cancel it through the facility
    if 'template' in obs.observation_id:
        last_obs = obs.observationgroup_set.first().observation_records.all().exclude(observation_id__contains='template').order_by('-id').first()
        if last_obs is None:
            # Sequence was canceled before a request was submitted to the observation portal
            obs_group = obs.observationgroup_set.first()
            dynamic_cadence = DynamicCadence.objects.get(observation_group=obs_group)
            dynamic_cadence.active = False
            dynamic_cadence.save()
            
            # Update the end date in the template record
            obs.parameters['sequence_end'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
            obs.save()
            
            return True
    else:
        last_obs = obs
    
    facility = get_service_class(last_obs.facility)()
    ocs_settings = LCOSettings()
    if last_obs.status not in ocs_settings.get_terminal_observing_states():
        success = facility.cancel_observation(last_obs.observation_id)
        if not success:
            return False

        last_obs.status = 'CANCELED'
        last_obs.save()
    
    ## Change status of DynamicCadence
    obs_group = obs.observationgroup_set.first()
    dynamic_cadence = DynamicCadence.objects.get(observation_group=obs_group)
    dynamic_cadence.active = False
    dynamic_cadence.save()

    ## Update sequence end time in template record
    template = obs_group.observation_records.filter(observation_id='template').first()
    if template:
        template.parameters['sequence_end'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
        template.save()
    
    return True


def observation_sequence_cancel_view(request):
    
    obsr_id = int(float(request.GET['pk']))
    obsr = ObservationRecord.objects.get(id=obsr_id)
    # obsr is the template observation record, so need to get the most recent one from this sequence to cancel
    last_obs = obsr.observationgroup_set.first().observation_records.all().exclude(observation_id__contains='template').order_by('-id').first()

    if last_obs:
        canceled = cancel_observation(last_obs)
        
        if not canceled:
            response_data = {'failure': 'Error'}
            return HttpResponse(json.dumps(response_data), content_type='application/json')
    
    ## Run hook to cancel old sequence in SNEx1
    try:
        obs_group = obsr.observationgroup_set.first()
        snex_id = int(obs_group.name)
        # Get comments, if any
        comments = json.loads(request.GET['comment'])
        if comments.get('cancel', ''):
            save_comments(comments['cancel'], obs_group.id, request.user)
            run_hook('cancel_sequence_in_snex1', 
                     snex_id, 
                     comment=comments['cancel'],
                     tableid=snex_id,
                     userid=request.user.id,
                     targetid=obsr.target_id)
        else:
            run_hook('cancel_sequence_in_snex1', snex_id, userid=request.user.id)
    except:
        logger.error('This sequence was not in SNEx1 or was not canceled', exc_info=True)
    
    response_data = {'success': 'Modified'}
    return HttpResponse(json.dumps(response_data), content_type='application/json')


def approve_or_reject_observation_view(request):
    
    obsr_id = int(float(request.GET['pk']))
    status = request.GET['status']
    obsr = ObservationRecord.objects.get(id=obsr_id)
    obsr.observation_id = 'template'
    obsr.save()

    obs_group = obsr.observationgroup_set.first()

    if status == 'approved':
        ## Set the cadence to active in SNEx2 and approve it in SNEx1
        cadence = DynamicCadence.objects.get(observation_group_id=obs_group.id)
        cadence.active = True
        cadence.save()
        
        try:
            snex_id = int(obs_group.name)
            run_hook('approve_sequence_in_snex1', snex_id)
        except:
            response_data = {'failure': 'Error'}
            logger.error('This sequence was not in SNEx1 or was not canceled')
            return HttpResponse(json.dumps(response_data), content_type='application/json')
    
    elif status == 'rejected':
        ## Set the end time for the template in SNEx2, and cancel it in SNEx1
        obsr.parameters['sequence_end'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
        obsr.save()
        
        comments = json.loads(request.GET['comment'])
        try:
            snex_id = int(obs_group.name)
            if comments.get('cancel', ''):
                save_comments(comments['cancel'], obs_group.id, request.user)
                run_hook('cancel_sequence_in_snex1', 
                         snex_id, 
                         comment=comments['cancel'],
                         tableid=snex_id,
                         userid=request.user.id,
                         targetid=obsr.target_id)
            else:
                run_hook('cancel_sequence_in_snex1', snex_id, userid=request.user.id)
        except:
            response_data = {'failure': 'Error'}
            logger.error('This sequence was not in SNEx1 or was not canceled')
            return HttpResponse(json.dumps(response_data), content_type='application/json')
     
    response_data = {'success': 'Modified'}
    return HttpResponse(json.dumps(response_data), content_type='application/json')


def scheduling_view(request):

    if 'modify' in request.GET['button']:
        obs_id = int(float(request.GET['observation_id']))
        obs = ObservationRecord.objects.get(id=obs_id)

        ## Get the new observation parameters
        form_data = {'name': request.GET['name'],
                     'target_id': int(float(request.GET['target_id'])),
                     'facility': request.GET['facility'],
                     'observation_type': request.GET['observation_type']
            }

        observing_parameters = json.loads(request.GET['observing_parameters'])
        # Append the additional info that users can change to parameters
        observing_parameters['ipp_value'] = float(request.GET['ipp_value'])
        observing_parameters['max_airmass'] = float(request.GET['max_airmass'])
        observing_parameters['cadence_strategy'] = request.GET.get('cadence_strategy', '')
        observing_parameters['cadence_frequency'] = float(request.GET['cadence_frequency'])
        observing_parameters['reminder'] = float(request.GET['reminder']) #Observing form turns this into timestamp
        observing_parameters['facility'] = obs.facility
        observing_parameters['name'] = form_data['name']
        observing_parameters['target_id'] = form_data['target_id']
        observing_parameters['delay_start'] = True
        observing_parameters['delay_amount'] = float(request.GET['delay_start'])
        
        now = datetime.utcnow()
        observing_parameters['start'] = datetime.strftime(now + timedelta(days=float(request.GET['delay_start'])), '%Y-%m-%dT%H:%M:%S')
        observing_parameters['end'] = datetime.strftime(now + timedelta(hours=float(request.GET['cadence_frequency'])*24+float(request.GET['delay_start'])*24), '%Y-%m-%dT%H:%M:%S')

        if request.GET['observation_type'] == 'IMAGING':
            filters = ['U', 'B', 'V', 'R', 'I', 'u', 'gp', 'rp', 'ip', 'zs', 'w']
            for f in filters:
                if f+'_0' in request.GET.keys() and float(request.GET[f+'_0'][0]) > 0.0:
                    observing_parameters[f] = [float(request.GET[f+'_0']), int(float(request.GET[f+'_1'])), int(float(request.GET[f+'_2']))]

        elif request.GET['observation_type'] == 'SPECTRA':
            observing_parameters['exposure_time'] = int(float(request.GET['exposure_time']))

        if request.GET['cadence_strategy']: 
            cadence = {'cadence_strategy': request.GET['cadence_strategy'],
                       'cadence_frequency': float(request.GET['cadence_frequency'])
                }
            form_data['cadence'] = cadence 
        form_data['observing_parameters'] = observing_parameters

        # Make sure at least one of the observing parameters changed
        dict_keys = ['ipp_value', 'max_airmass', 'cadence_frequency', 'U', 'B', 'V', 'R', 'I', 'u', 'gp', 'rp', 'ip', 'zs', 'w', 'exposure_time']
        modified = False
        for key in dict_keys:
            if key in observing_parameters.keys() and key in obs.parameters.keys():
                if observing_parameters[key] != obs.parameters[key]:
                    modified = True
                    break
        if not modified:
            response_data = {'failure': 'Sequence parameters were not modified, please modify one and try again'}
            return HttpResponse(json.dumps(response_data), content_type='application/json')

        ### Begin atomic transaction here
        try:
            db_session = _return_session()
            with transaction.atomic():
                
                ### Get SNEx1 db session
                
                # Cancel the old observation
                canceled = cancel_observation(obs)
                if not canceled:
                    response_data = {'failure': 'Canceling the previous sequence failed, please try again'}
                    raise Snex1ConnectionError(message='Could not cancel previous sequence')
                    #return HttpResponse(json.dumps(response_data), content_type='application/json')
        
                # Submission follows how observation requests are submitted in TOM view
                facility = get_service_class(obs.facility)()
                form = facility.get_form(form_data['observation_type'])(observing_parameters)
                if form.is_valid():
                    observation_errors = facility.validate_observation(form.observation_payload())
                    if observation_errors:
                        logger.error(msg=f'Unable to submit next cadenced observation: {observation_errors}')
                        response_data = {'failure': 'Unable to submit next cadenced observation'}
                        raise Snex1ConnectionError(message='Observation portal returned errors {}'.format(observation_errors))

                else:
                    logger.error(msg=f'Unable to submit next cadenced observation: {form.errors}')
                    response_data = {'failure': 'Unable to submit next cadenced observation'}
                    raise Snex1ConnectionError(message='Observation portal returned errors {}'.format(form.errors))
                    #return HttpResponse(json.dumps(response_data), content_type='application/json')

                # Creation of corresponding ObservationRecord objects for the observations
                new_observations = []
                for observation_id in ['template']:#observation_ids:
                    # Create Observation record
                    record = ObservationRecord.objects.create(
                        target=Target.objects.get(id=form_data['target_id']),
                        facility=facility.name,
                        parameters=form.serialize_parameters(),#observing_parameters,
                        observation_id=observation_id
                    )
                    # Add the request user
                    record.parameters['start_user'] = request.user.first_name
                    record.save()
                    new_observations.append(record)
        
                if len(new_observations) > 1 or form_data.get('cadence'):
                    observation_group = ObservationGroup.objects.create(name=form_data['name'])
                    observation_group.observation_records.add(*new_observations)
                    assign_perm('tom_observations.view_observationgroup', request.user, observation_group)
                    assign_perm('tom_observations.change_observationgroup', request.user, observation_group)
                    assign_perm('tom_observations.delete_observationgroup', request.user, observation_group)

                    if form_data.get('cadence'):
                        DynamicCadence.objects.create(
                            observation_group=observation_group,
                            cadence_strategy=cadence.get('cadence_strategy'),
                            cadence_parameters={'cadence_frequency': float(request.GET['cadence_frequency'])},
                            active=True
                        )

                if not settings.TARGET_PERMISSIONS_ONLY:
                    group_id_list = list(GroupObjectPermission.objects.filter(object_pk=obs_id).values_list('group_id', flat=True).distinct())
                    groups = Group.objects.filter(id__in=group_id_list)
                    for record in new_observations:
                        assign_perm('tom_observations.view_observationrecord', groups, record)
                        assign_perm('tom_observations.change_observationrecord', groups, record)
                        assign_perm('tom_observations.delete_observationrecord', groups, record)
        
                ### Sync with SNEx1
                ## Run hook to cancel old sequence in SNEx1
                obs_group = obs.observationgroup_set.first()
                snex_id = int(obs_group.name)
            
                # Get comments, if any
                comments = json.loads(request.GET['comment'])
                if comments.get('cancel', ''):
                    save_comments(comments['cancel'], obs_group.id, request.user)
                    run_hook('cancel_sequence_in_snex1',
                             snex_id,
                             comment=comments['cancel'],
                             tableid=snex_id,
                             userid=request.user.id,
                             targetid=obs.target_id,
                             wrapped_session=db_session)
                else:
                    run_hook('cancel_sequence_in_snex1', 
                             snex_id, 
                             userid=request.user.id, 
                             wrapped_session=db_session)
        
                # Get the group ids to pass to SNEx1
                group_names = []
                if not settings.TARGET_PERMISSIONS_ONLY:
                    for group in groups:
                        group_names.append(group.name)
        
                # Run the hook to add the sequence to SNEx1
                # Get comments, if any
                snex_id = run_hook(
                        'sync_sequence_with_snex1', 
                        form.serialize_parameters(), 
                        group_names, 
                        userid=request.user.id,
                        wrapped_session=db_session)
        
                # Change the name of the observation group, if one was created
                if len(new_observations) > 1 or form_data.get('cadence'):
                    observation_group.name = str(snex_id)
                    observation_group.save()

                    for record in new_observations:
                        record.parameters['name'] = snex_id
                        record.save()
            
                ## Now run the hook to add each observation record to SNEx1
                #for record in new_observations:
                #    # Get the requestsgroup ID from the LCO API using the observation ID
                #    obs_id = int(record.observation_id)
                #    LCO_SETTINGS = settings.FACILITIES['LCO']
                #    PORTAL_URL = LCO_SETTINGS['portal_url']
                #    portal_headers = {'Authorization': 'Token {0}'.format(LCO_SETTINGS['api_key'])}

                #    query_params = urlencode({'request_id': obs_id})

                #    r = requests.get('{}/api/requestgroups?{}'.format(PORTAL_URL, query_params), headers=portal_headers)
                #    requestgroups = r.json()
                #    if requestgroups['count'] == 1:
                #        requestgroup_id = int(requestgroups['results'][0]['id'])

                #    run_hook('sync_observation_with_snex1', snex_id, record.parameters, requestgroup_id, wrapped_session=db_session)
                
                response_data = {'success': 'Modified'}
                db_session.commit()

        except Exception as e: 
            logger.error('Syncing with the SNEx1 database failed for target {} with error {}'.format(obs.target_id, e))
            response_data = {'failure': 'Unable to modify sequence: see logs for error'}
            db_session.rollback()
        
        finally:
            db_session.close()
        
        ### End of the atomic transaction
        return HttpResponse(json.dumps(response_data), content_type='application/json')

    elif 'continue' in request.GET['button']:
        logger.info('Continuing Sequence as-is')
        observation_id = int(float(request.GET['observation_id']))
        obs = ObservationRecord.objects.get(id=observation_id)
        
        ## Check to make sure no parameters were updated
        observing_parameters = {}
        observing_parameters['ipp_value'] = float(request.GET['ipp_value'])
        observing_parameters['max_airmass'] = float(request.GET['max_airmass'])
        observing_parameters['cadence_frequency'] = float(request.GET['cadence_frequency'])
        
        if request.GET['observation_type'] == 'IMAGING':
            filters = ['U', 'B', 'V', 'R', 'I', 'u', 'gp', 'rp', 'ip', 'zs', 'w']
            for f in filters:
                if f+'_0' in request.GET.keys() and float(request.GET[f+'_0'][0]) > 0.0:
                    observing_parameters[f] = [float(request.GET[f+'_0']), int(float(request.GET[f+'_1'])), int(float(request.GET[f+'_2']))]

        elif request.GET['observation_type'] == 'SPECTRA':
            observing_parameters['exposure_time'] = int(float(request.GET['exposure_time']))
        
        dict_keys = ['ipp_value', 'max_airmass', 'cadence_frequency', 'U', 'B', 'V', 'R', 'I', 'u', 'gp', 'rp', 'ip', 'zs', 'w', 'exposure_time']
        modified = False
        for key in dict_keys:
            if key in observing_parameters.keys() and key in obs.parameters.keys():
                if observing_parameters[key] != obs.parameters[key]:
                    modified = True
                    break
        if modified:
            response_data = {'failure': 'Sequence parameters were modified. If this was intentional, please press the "Modify Sequence" button instead.'}
            return HttpResponse(json.dumps(response_data), content_type='application/json')

        ## Only update the reminder parameter in ObservationRecord
        try:
            db_session = _return_session()
            with transaction.atomic():
                next_reminder = float(request.GET['reminder'])
                obs_parameters = obs.parameters
                now = datetime.now()
                obs_parameters['reminder'] = datetime.strftime(now + timedelta(days=next_reminder), '%Y-%m-%dT%H:%M:%S')
                obs.parameters = obs_parameters
                obs.save()
                
                ## Run hook to update the reminder in SNEx1
                obsgroup = obs.observationgroup_set.first()
                snex_id = int(obsgroup.name)
                run_hook('update_reminder_in_snex1', snex_id, next_reminder, wrapped_session=db_session)
                response_data = {'success': 'Continued'}
                db_session.commit()

        except:
            message = 'This sequence was not in SNEx1 or the reminder was not updated'
            logger.error(message)
            response_data = {'failure': message}
            db_session.rollback()

        finally:
            db_session.close()
                
        return HttpResponse(json.dumps(response_data), content_type='application/json')
    
    elif 'stop' in request.GET['button']:
        logger.info('Stopping Sequence')
        ## Cancel observation request in LCO portal
        try:
            db_session = _return_session()
            with transaction.atomic():
                obs_id = int(float(request.GET['observation_id']))
                obs = ObservationRecord.objects.get(id=obs_id)
                canceled = cancel_observation(obs)
                if not canceled:
                    response_data = {'failure': 'This sequence could not be canceled in SNEx1'} 
                    raise Snex1ConnectionError(message='This sequence could not be canceled in SNEx1')
                ## Run hook to cancel this sequence in SNEx1
                obs_group = obs.observationgroup_set.first()
                snex_id = int(obs_group.name)

                # Get comments, if any
                comments = json.loads(request.GET['comment'])
                if comments.get('cancel', ''):
                    save_comments(comments['cancel'], obs_group.id, request.user)
                    run_hook('cancel_sequence_in_snex1', 
                             snex_id, 
                             comment=comments['cancel'],
                             tableid=snex_id,
                             userid=request.user.id,
                             targetid=obs.target_id,
                             wrapped_session=db_session)
                else:
                    run_hook('cancel_sequence_in_snex1', snex_id, userid=request.user.id, wrapped_session=db_session)
        
                response_data = {'success': 'Stopped'}
                db_session.commit()
        
        except:
            message = 'This sequence was not in SNEx1 or was not canceled'
            logger.error(message)
            response_data = {'failure': message}
            db_session.rollback()

        finally:
            db_session.close()

        return HttpResponse(json.dumps(response_data), content_type='application/json')


def change_target_known_to_view(request):
    action = request.GET.get('action')
    group_name = request.GET.get('group')
    group = Group.objects.get(name=group_name)
    target_name = request.GET.get('target')
    target = Target.objects.get(name=target_name)
    
    if target not in get_objects_for_user(request.user, 'tom_targets.change_target'):
        response_data = {'failure': 'Error'}
        return HttpResponse(json.dumps(response_data), content_type='application/json')

    if action == 'add':
        # Add permissions for this group
        assign_perm('tom_targets.view_target', group, target)
        assign_perm('tom_targets.change_target', group, target)
        assign_perm('tom_targets.delete_target', group, target)
        response_data = {'success': 'Added'}
        return HttpResponse(json.dumps(response_data), content_type='application/json')

    elif action == 'remove':
        # Remove permissions for this group
        remove_perm('tom_targets.view_target', group, target)
        remove_perm('tom_targets.change_target', group, target)
        remove_perm('tom_targets.delete_target', group, target)
        response_data = {'success': 'Removed'}
        return HttpResponse(json.dumps(response_data), content_type='application/json')
        

class ReferenceStatusUpdateView(FormView):

    form_class = ReferenceStatusForm
    template_name = 'custom_code/reference_status.html'

    def form_valid(self, form):
        target_id = form.cleaned_data['target']
        target = Target.objects.get(id=target_id)
        status = form.cleaned_data['status']
        target.reference = status
        target.save()

        return HttpResponseRedirect('/targets/{}/'.format(target.id))


def change_interest_view(request):
    target_name = request.GET.get('target')
    target = Target.objects.get(name=target_name)
    user = request.user

    interested_persons = [p.user for p in InterestedPersons.objects.filter(target=target)]
    if user in interested_persons:
        user_interest_row = InterestedPersons.objects.get(target=target, user=user)
        user_interest_row.delete()

        run_hook('change_interest_in_snex1', target.id, user.username, 'uninterested')

        response_data = {'success': 'Uninterested'}
        return HttpResponse(json.dumps(response_data), content_type='application/json')

    else:
        user_interest_row = InterestedPersons(target=target, user=user)
        user_interest_row.save()

        run_hook('change_interest_in_snex1', target.id, user.username, 'interested')
        
        response_data = {'success': 'Interested',
                         'name': user.get_full_name()
                    }
        return HttpResponse(json.dumps(response_data), content_type='application/json')


def search_name_view(request):

    search_entry = request.GET.get('name')
    logger.info("searching for {}".format(search_entry))
    context = {}
    if search_entry:
        target_match_list = Target.objects.filter(Q(name__icontains=search_entry) | Q(aliases__name__icontains=search_entry)).distinct()

    else:
        target_match_list = Target.objects.none()

    context['targets'] = target_match_list
    
    if request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest':
        html = render_to_string(
            template_name='custom_code/partials/name-search-results.html',
            context={'targets': target_match_list}
        )

        data_dict = {"html_from_view": html}

        return JsonResponse(data=data_dict, safe=False)
    return render(request, 'tom_targets/target_grouping.html', context=context)


def async_spectra_page_view(request):
    target_id = request.GET.get('target_id')
    if target_id:
        target = Target.objects.get(id=target_id)
        context = custom_code_tags.dash_spectra_page(RequestContext(request), target)
        html = render_to_string(
            template_name='custom_code/dash_spectra_page.html',
            context=context,
            request=request
        )
        data_dict = {'html_from_view': html}

        return JsonResponse(data=data_dict, safe=False)
    return ''


def async_scheduling_page_view(request):
    obs_ids = json.loads(request.GET['obs_ids'])
    all_html = ''
    for obs_id in obs_ids:
        obs = ObservationRecord.objects.get(id=obs_id)
        response = custom_code_tags.scheduling_list_with_form({'request': request}, obs, case='nonpending')

        html = render_to_string(
            template_name='custom_code/scheduling_list_with_form.html',
            context=response,
            request=request
        )

        all_html += html

    data_dict = {'html_from_view': all_html}
    
    return JsonResponse(data=data_dict, safe=False)


def add_target_to_group_view(request):
    target_name = request.GET.get('target_name')

    target = Target.objects.filter(Q(name__icontains=target_name) | Q(aliases__name__icontains=target_name)).first()

    targetlist_id = request.GET.get('group_id')
    targetlist = TargetList.objects.get(id=targetlist_id)

    list_type = request.GET.get('list')

    if request.user.has_perm('custom_code.view_target', target) and target not in targetlist.targets.all():

        if list_type == 'observing_run':
            if len(targetlist.targets.all()) == 0:
                target_priority = 1
            else:
                #currently targetlist.targets.all() is a list of basetargets, needs to be snextargets
                target_priority = max([Target.objects.get(pk=t.pk).observing_run_priority for t in targetlist.targets.all()]) + 1
            target.observing_run_priority = target_priority
            target.save()
        
        targetlist.targets.add(target)
    
    response_data = {'success': 'Added'}
    return HttpResponse(json.dumps(response_data), content_type='application/json') 


def remove_target_from_group_view(request):
    target_id = request.GET.get('target_id')
    target = Target.objects.get(id=target_id)
    
    targetlist_id = request.GET.get('group_id')
    targetlist = TargetList.objects.get(id=targetlist_id)
    
    list_type = request.GET.get('list')

    if request.user.has_perm('tom_targets.view_target', target) and target in targetlist.targets.all():
        targetlist.targets.remove(target)

        if list_type == 'observing_run': 
            old_priority = target.observing_run_priority or 0

            if len(targetlist.targets.all()) > 0:
                for t in targetlist.targets.all():
                    if t.observing_run_priority > old_priority:
                        t.observing_run_priority -= 1
                        t.save()
            
            target.observing_run_priority = None
            target.save()
        
    response_data = {'success': 'Removed'}
    return HttpResponse(json.dumps(response_data), content_type='application/json') 


def change_observing_priority_view(request):
    target_id = request.GET.get('target_id')
    target = Target.objects.get(id=target_id)

    targetlist_id = request.GET.get('group_id')
    targetlist = TargetList.objects.get(id=targetlist_id)

    try:
        new_priority = int(request.GET.get('priority'))
    except:
        new_priority = int(float(request.GET.get('priority')))

    target.observing_run_priority = new_priority
    target.save()

    for t in targetlist.targets.all():
        if t == target:
            continue
        if t.observing_run_priority >= new_priority:
            t.observing_run_priority += 1
            t.save()
    return HttpResponseRedirect('/targets/targetgrouping/')


class CustomObservationListView(ObservationListView):

    def get_queryset(self, *args, **kwargs):
        """
        Gets the most recent ObservationRecord objects associated with active
        DynamicCadences that the user has permission to view
        """
        try:
            obsrecordlist = [c.observation_group.observation_records.order_by('-created').first() for c in DynamicCadence.objects.filter(active=True)]
        except Exception as e:
            logger.info(e)
            obsrecordlist = []
        obsrecordlist_ids = [o.id for o in obsrecordlist if o is not None and self.request.user in get_users_with_perms(o)]
        return ObservationRecord.objects.filter(id__in=obsrecordlist_ids)


class ObservationListExtrasView(ListView):
    """
    View that displays all active sequences by either IPP or urgency
    """
    template_name = 'custom_code/observation_list_extras.html'
    paginate_by = 10
    model = ObservationRecord
    strict = False
    context_object_name = 'observation_list'

    def get_queryset(self, *args, **kwargs):
        """
        Get all active cadences and order their observation records in order of IPP or urgency
        """
        val = self.kwargs['key']
        
        if val == 'ipp':
            try:
                obsrecordlist = [c.observation_group.observation_records.order_by('-created').first() for c in DynamicCadence.objects.filter(active=True)]
            except Exception as e:
                logger.info(e)
                obsrecordlist = []
            obsrecordlist_ids = [o.id for o in obsrecordlist if o is not None and self.request.user in get_users_with_perms(o)]
            obsrecords = ObservationRecord.objects.filter(id__in=obsrecordlist_ids)
            return obsrecords.order_by('-parameters__ipp_value')
        
        elif val == 'urgency':
            try:
                obsrecordlist = [c.observation_group.observation_records.filter(status='COMPLETED').order_by('-created').first() for c in DynamicCadence.objects.filter(active=True)]
            except Exception as e:
                logger.info(e)
                obsrecordlist = []
            obsrecordlist_ids = [o.id for o in obsrecordlist if o is not None and self.request.user in get_users_with_perms(o)]
            obsrecords = ObservationRecord.objects.filter(id__in=obsrecordlist_ids)
            now = datetime.utcnow()
            recent_obs = obsrecords.annotate(days_since=now-Cast(KeyTextTransform('start', 'parameters'), DateTimeField()))
            recent_obs = recent_obs.filter(parameters__cadence_frequency__gt=0.0)
            recent_obs = recent_obs.annotate(urgency=ExpressionWrapper(F('days_since')/(Cast(KeyTextTransform('cadence_frequency', 'parameters'), FloatField())), DateTimeField()))
            return recent_obs.order_by('-urgency')

    
    def get_context_data(self, *args, **kwargs):
        
        context = super().get_context_data(*args, **kwargs)
        context['value'] = self.kwargs['key'].upper()
        return context


class CustomObservationCreateView(ObservationCreateView):

    def get_form(self):
        """
        Gets an instance of the form appropriate for the request.
        :returns: observation form
        :rtype: subclass of GenericObservationForm
        """
        form = super().get_form()
        if not settings.TARGET_PERMISSIONS_ONLY:
            form.fields['groups'].queryset = Group.objects.all()
        form.helper.form_action = reverse(
            'submit-lco-obs', kwargs={'facility': 'LCO'}
        )
        return form
    
    
    def form_valid(self, form):
        """
        Runs after form validation. Submits the observation to the desired facility and creates an associated
        ``ObservationRecord``, then writes this sequence and record to the SNEx1 database,
        and finally redirects to the detail page of the target to be observed.
        If the facility returns more than one record, a group is created and all observation
        records from the request are added to it.
        :param form: form containing observating request parameters
        :type form: subclass of GenericObservationForm
        """
        # Submit the observation
        facility = self.get_facility_class()
        target = self.get_target()
        errors = facility().validate_observation(form.observation_payload()) #TODO: Do something with errors
        records = []

        for observation_id in ['template']:#observation_ids:
            # Create Observation record
            record = ObservationRecord.objects.create(
                target=target,
                user=self.request.user,
                facility=facility.name,
                parameters=form.serialize_parameters(),
                observation_id=observation_id
            )
            # Add the request user
            record.parameters['start_user'] = self.request.user.first_name
            record.save()
            records.append(record)

        if len(records) > 1 or form.cleaned_data.get('cadence_strategy'):
            observation_group = ObservationGroup.objects.create(name=form.cleaned_data['name'])
            observation_group.observation_records.add(*records)
            assign_perm('tom_observations.view_observationgroup', self.request.user, observation_group)
            assign_perm('tom_observations.change_observationgroup', self.request.user, observation_group)
            assign_perm('tom_observations.delete_observationgroup', self.request.user, observation_group)

            if form.cleaned_data.get('cadence_strategy'):
                cadence_parameters = {}
                cadence_form = get_cadence_strategy(form.cleaned_data.get('cadence_strategy')).form
                for field in cadence_form().cadence_fields:
                    cadence_parameters[field] = form.cleaned_data.get(field)
                DynamicCadence.objects.create(
                    observation_group=observation_group,
                    cadence_strategy=form.cleaned_data.get('cadence_strategy'),
                    cadence_parameters=cadence_parameters,
                    active=True
                )

        if not settings.TARGET_PERMISSIONS_ONLY:
            groups = form.cleaned_data['groups']
            for record in records:
                assign_perm('tom_observations.view_observationrecord', groups, record)
                assign_perm('tom_observations.change_observationrecord', groups, record)
                assign_perm('tom_observations.delete_observationrecord', groups, record)
        
        ### Sync with SNEx1
        
        # Get the group ids to pass to SNEx1
        group_names = []
        for group in form.cleaned_data['groups']:
           group_names.append(group.name)
        
        # Run the hook to add the sequence to SNEx1
        if form.cleaned_data.get('comment') and (len(records) > 1 or form.cleaned_data.get('cadence_strategy')):
            save_comments(form.cleaned_data['comment'], observation_group.id, self.request.user)
            snex_id = run_hook('sync_sequence_with_snex1', 
                               form.serialize_parameters(), 
                               group_names, 
                               userid=self.request.user.id, 
                               comment=form.cleaned_data['comment'], 
                               targetid=target.id)
            
        else:
            snex_id = run_hook('sync_sequence_with_snex1', form.serialize_parameters(), group_names, userid=self.request.user.id)

        if settings.DEBUG and snex_id is None:
            snex_id = str(random.randint(0, 99999))
        
        # Change the name of the observation group, if one was created
        if len(records) > 1 or form.cleaned_data.get('cadence_strategy'):
            observation_group.name = str(snex_id)
            observation_group.save()

            for record in records:
                record.parameters['name'] = snex_id
                record.save()
            
        ## Now run the hook to add each observation record to SNEx1
        #for record in records:
        #    # Get the requestsgroup ID from the LCO API using the observation ID
        #    obs_id = int(record.observation_id)
        #    LCO_SETTINGS = settings.FACILITIES['LCO']
        #    PORTAL_URL = LCO_SETTINGS['portal_url']
        #    portal_headers = {'Authorization': 'Token {0}'.format(LCO_SETTINGS['api_key'])}

        #    query_params = urlencode({'request_id': obs_id})

        #    r = requests.get('{}/api/requestgroups?{}'.format(PORTAL_URL, query_params), headers=portal_headers)
        #    requestgroups = r.json()
        #    if requestgroups['count'] == 1:
        #        requestgroup_id = int(requestgroups['results'][0]['id'])

        #    run_hook('sync_observation_with_snex1', snex_id, record.parameters, requestgroup_id)

        return redirect(
            reverse('tom_targets:detail', kwargs={'pk': target.id})
        )


def make_tns_request_view(request):
    target_id = request.GET.get('target_id')
    target = Target.objects.get(id=target_id)

    tns_params = _get_tns_params(target)
    if tns_params.get('success', ''):
        return HttpResponse(json.dumps(tns_params), content_type='application/json')
    else:
        logger.info('TNS parameters not ingested for target {}'.format(target_id))
        response_data = {'failure': 'TNS parameters not ingested for this target'}
        return HttpResponse(json.dumps(response_data), content_type='application/json')


def load_lightcurve_view(request):
    target = Target.objects.get(id=request.GET.get('target_id'))
    user = User.objects.get(id=request.GET.get('user_id'))

    lightcurve = custom_code_tags.lightcurve_with_extras(target, user)['plot']
    context = {'success': 'success',
               'lightcurve_plot': lightcurve
    }
    return HttpResponse(json.dumps(context), content_type='application/json')


def load_dash_lightcurve_view(request):
    """Lazy-load the dash lightcurve plot for the overview tab"""
    target_id = request.GET.get('target_id')
    width = int(request.GET.get('width', 600))
    height = int(request.GET.get('height', 400))
    
    if target_id:
        target = Target.objects.get(id=target_id)
        # Create a context dict with request, as expected by dash_lightcurve
        context_dict = {'request': request}
        context = custom_code_tags.dash_lightcurve(context_dict, target, width, height)
        html = render_to_string(
            template_name='custom_code/dash_lightcurve.html',
            context=context,
            request=request
        )
        return JsonResponse({'plot_html': html}, safe=False)
    return JsonResponse({'plot_html': '<div>Error loading plot</div>'}, safe=False)


def load_spectra_plot_view(request):
    """Lazy-load the spectra plot for the overview tab"""
    target_id = request.GET.get('target_id')
    
    if target_id:
        target = Target.objects.get(id=target_id)
        # Create a context dict with request, as expected by spectra_plot
        context_dict = {'request': request}
        context = custom_code_tags.spectra_plot(context_dict, target)
        html = render_to_string(
            template_name='custom_code/spectra.html',
            context=context,
            request=request
        )
        return JsonResponse({'plot_html': html}, safe=False)
    return JsonResponse({'plot_html': '<div>Error loading plot</div>'}, safe=False)


def load_thumbnail_view(request):
    """Lazy-load the thumbnail for the overview tab"""
    target_id = request.GET.get('target_id')
    
    if target_id:
        target = Target.objects.get(id=target_id)
        # Create a context dict with request, as expected by test_display_thumbnail
        context_dict = {'request': request}
        context = custom_code_tags.test_display_thumbnail(context_dict, target)
        html = render_to_string(
            template_name='custom_code/thumbnail.html',
            context=context,
            request=request
        )
        return JsonResponse({'html': html}, safe=False)
    return JsonResponse({'html': '<div>Error loading thumbnail</div>'}, safe=False)


def load_airmass_plot_view(request):
    """Lazy-load the airmass plot for the overview tab"""
    target_id = request.GET.get('target_id')
    
    if target_id:
        target = Target.objects.get(id=target_id)
        # Create a context dict with object (not request), as expected by airmass_plot
        context_dict = {'object': target}
        context = custom_code_tags.airmass_plot(context_dict)
        html = render_to_string(
            template_name='custom_code/airmass.html',
            context=context,
            request=request
        )
        return JsonResponse({'html': html}, safe=False)
    return JsonResponse({'html': '<div>Error loading airmass plot</div>'}, safe=False)


def load_details_tab_view(request):
    """Lazy-load the details tab content"""
    target_id = request.GET.get('target_id')
    
    if target_id:
        target = Target.objects.get(id=target_id)
        context_dict = {'request': request, 'user': request.user}
        context = custom_code_tags.target_details(context_dict, target)
        html = render_to_string(
            template_name='custom_code/target_details.html',
            context=context,
            request=request
        )
        return JsonResponse({'html': html}, safe=False)
    return JsonResponse({'html': '<div>Error loading details</div>'}, safe=False)


def load_observations_tab_view(request):
    """Lazy-load the observations tab content"""
    target_id = request.GET.get('target_id')
    
    if target_id:
        target = Target.objects.get(id=target_id)
        context_dict = {'request': request, 'user': request.user}
        
        # Get all the template tag contexts
        observing_buttons_context = observing_buttons(target)
        previous_obs_context = custom_code_tags.observation_summary(context_dict, target, 'previous')
        ongoing_obs_context = custom_code_tags.observation_summary(context_dict, target, 'ongoing')
        pending_obs_context = custom_code_tags.observation_summary(context_dict, target, 'pending')
        submit_obs_context = custom_code_tags.submit_lco_observations(target)
        
        # Combine all contexts
        combined_context = {
            'target': target,
            'object': target,
            'observing_buttons_html': render_to_string('tom_observations/partials/observing_buttons.html', observing_buttons_context, request=request),
            'previous_obs_html': render_to_string('custom_code/observation_summary.html', previous_obs_context, request=request),
            'ongoing_obs_html': render_to_string('custom_code/observation_summary.html', ongoing_obs_context, request=request),
            'pending_obs_html': render_to_string('custom_code/observation_summary.html', pending_obs_context, request=request),
            'submit_obs_html': render_to_string('custom_code/submit_lco_observations.html', submit_obs_context, request=request),
        }
        
        # Render the combined template
        html = render_to_string(
            template_name='custom_code/observations_tab.html',
            context=combined_context,
            request=request
        )
        return JsonResponse({'html': html}, safe=False)
    return JsonResponse({'html': '<div>Error loading observations</div>'}, safe=False)


def load_manage_data_tab_view(request):
    """Lazy-load the manage data tab content"""
    target_id = request.GET.get('target_id')
    
    if target_id:
        target = Target.objects.get(id=target_id)
        context_dict = {'request': request, 'user': request.user}
        
        # Get the template tag contexts
        upload_context = custom_code_tags.custom_upload_dataproduct(context_dict, target) if request.user.is_authenticated else None
        dataproduct_context = dataproduct_list_for_target(context_dict, target)
        
        # Render the components
        upload_html = render_to_string('custom_code/custom_upload_dataproduct.html', upload_context, request=request) if upload_context else ''
        dataproduct_html = render_to_string('tom_dataproducts/partials/dataproduct_list_for_target.html', dataproduct_context, request=request)
        
        combined_context = {
            'upload_html': upload_html,
            'dataproduct_html': dataproduct_html,
        }
        
        html = render_to_string(
            template_name='custom_code/manage_data_tab.html',
            context=combined_context,
            request=request
        )
        return JsonResponse({'html': html}, safe=False)
    return JsonResponse({'html': '<div>Error loading manage data</div>'}, safe=False)



def load_observing_runs_tab_view(request):
    """Lazy-load the observing runs tab content"""
    target_id = request.GET.get('target_id')
    
    if target_id:
        target = Target.objects.get(id=target_id)
        context = target_groups(target)
        html = render_to_string(
            template_name='tom_targets/partials/target_groups.html',
            context=context,
            request=request
        )
        return JsonResponse({'html': html}, safe=False)
    return JsonResponse({'html': '<div>Error loading observing runs</div>'}, safe=False)


def load_images_tab_view(request):
    """Lazy-load the images tab content"""
    target_id = request.GET.get('target_id')
    
    if target_id:
        target = Target.objects.get(id=target_id)
        context_dict = {'request': request, 'user': request.user}
        context = custom_code_tags.image_slideshow(context_dict, target)
        html = render_to_string(
            template_name='custom_code/image_slideshow.html',
            context=context,
            request=request
        )
        return JsonResponse({'html': html}, safe=False)
    return JsonResponse({'html': '<div>Error loading images</div>'}, safe=False)


def load_photometry_tab_view(request):
    """Lazy-load the photometry tab content"""
    target_id = request.GET.get('target_id')
    
    if target_id:
        target = Target.objects.get(id=target_id)
        context_dict = {'request': request, 'user': request.user}
        
        # Get photometry data
        photometry_context = custom_code_tags.snex2_get_photometry_data(context_dict, target)
        photometry_html = render_to_string('tom_dataproducts/partials/photometry_datalist_for_target.html', photometry_context, request=request)
        
        # Get dash lightcurve
        lightcurve_context = custom_code_tags.dash_lightcurve(context_dict, target, 1000, 600)
        lightcurve_html = render_to_string('custom_code/dash_lightcurve.html', lightcurve_context, request=request)
        
        combined_context = {
            'target': target,
            'photometry_html': photometry_html,
            'lightcurve_html': lightcurve_html,
        }
        
        html = render_to_string(
            template_name='custom_code/photometry_tab.html',
            context=combined_context,
            request=request
        )
        return JsonResponse({'html': html}, safe=False)
    return JsonResponse({'html': '<div>Error loading photometry</div>'}, safe=False)


def load_spectroscopy_tab_view(request):
    """Lazy-load the spectroscopy tab content"""
    target_id = request.GET.get('target_id')
    
    if target_id:
        target = Target.objects.get(id=target_id)
        context_dict = {'request': request, 'user': request.user}
        
        # Load the overview plot
        overview_context = custom_code_tags.dash_spectra(context_dict, target)
        overview_html = render_to_string(
            template_name='custom_code/dash_spectra.html',
            context=overview_context,
            request=request
        )
        
        # Get the list of spectra metadata without rendering the plots
        # Pass dict with request for compatibility (dash_spectra_page now supports both)
        details_context = custom_code_tags.dash_spectra_page(context_dict, target)
        
        # Build the spectra list metadata for progressive loading
        spectra_metadata = []
        for entry in details_context.get('plot_list', []):
            spectra_metadata.append({
                'spectrum_id': entry['spectrum'].id,
                'time': entry['time'],
                'telescope': entry['spec_extras'].get('telescope', 'Unknown'),
                'instrument': entry['spec_extras'].get('instrument', 'Unknown')
            })
        
        # Render the spectra container with placeholders
        spectra_container_html = render_to_string(
            template_name='custom_code/spectra_progressive_container.html',
            context={
                'spectra_metadata': spectra_metadata,
                'target': target,
                'target_data_share_form': details_context.get('target_data_share_form'),
                'sharing_destinations': details_context.get('sharing_destinations'),
                'hermes_sharing': details_context.get('hermes_sharing'),
            },
            request=request
        )
        
        # Combine both
        combined_html = overview_html + spectra_container_html
        
        return JsonResponse({'html': combined_html}, safe=False)
    return JsonResponse({'html': '<div>Error loading spectroscopy</div>'}, safe=False)


def load_single_spectrum_view(request):
    """Lazy-load a single spectrum"""
    spectrum_id = request.GET.get('spectrum_id')
    target_id = request.GET.get('target_id')
    
    if spectrum_id and target_id:
        try:
            target = Target.objects.get(id=target_id)
            spectrum = ReducedDatum.objects.get(id=spectrum_id)
            
            # Get spectrum extras
            user = User.objects.get(username=request.user)
            
            try:
                z = target.redshift
            except:
                z = 0
            
            # Get spectrum extras - query directly since user already has target access
            # (ReducedDatumExtra doesn't need separate object-level permissions)
            snex_id_row = ReducedDatumExtra.objects.filter(
                data_type='spectroscopy', target=target, 
                key='snex_id', value__icontains='"snex2_id": {}'.format(spectrum.id)
            ).first()
            
            spec_extras = {}
            if snex_id_row:
                snex1_id = json.loads(snex_id_row.value)['snex_id']
                spec_extras_row = ReducedDatumExtra.objects.filter(
                    data_type='spectroscopy', key='spec_extras', 
                    value__icontains='"snex_id": {}'.format(snex1_id)
                ).first()
                if spec_extras_row:
                    spec_extras = json.loads(spec_extras_row.value)
                    if spec_extras.get('instrument', '') == 'en06':
                        spec_extras['site'] = '(OGG 2m)'
                        spec_extras['instrument'] += ' (FLOYDS)'
                    elif spec_extras.get('instrument', '') == 'en12':
                        spec_extras['site'] = '(COJ 2m)'
                        spec_extras['instrument'] += ' (FLOYDS)'
                    
                    content_type_id = ContentType.objects.get(model='reduceddatum').id
                    comments = Comment.objects.filter(object_pk=spectrum.id, content_type_id=content_type_id).order_by('id')
                    comment_list = ['{}: {}'.format(comment.user.first_name, comment.comment) for comment in comments]
                    spec_extras['comments'] = comment_list
            elif spectrum.data_product_id:
                spec_extras_row = ReducedDatumExtra.objects.filter(
                    data_type='spectroscopy', key='upload_extras',
                    value__icontains='"data_product_id": {}'.format(spectrum.data_product_id)
                ).first()
                if spec_extras_row:
                    spec_extras = json.loads(spec_extras_row.value)
                    if spec_extras.get('instrument', '') == 'en06':
                        spec_extras['site'] = '(OGG 2m)'
                        spec_extras['instrument'] += ' (FLOYDS)'
                    elif spec_extras.get('instrument', '') == 'en12':
                        spec_extras['site'] = '(COJ 2m)'
                        spec_extras['instrument'] += ' (FLOYDS)'
                    
                    content_type_id = ContentType.objects.get(model='reduceddatum').id
                    comments = Comment.objects.filter(object_pk=spectrum.id, content_type_id=content_type_id).order_by('id')
                    comment_list = ['{}: {}'.format(comment.user.first_name, comment.comment) for comment in comments]
                    spec_extras['comments'] = comment_list
            
            # Calculate min/max flux
            datum = spectrum.value
            flux = []
            if datum.get('photon_flux'):
                flux = datum.get('photon_flux')
            elif datum.get('flux'):
                flux = datum.get('flux')
            else:
                for key, value in datum.items():
                    flux.append(float(value['flux']))
            
            max_flux = max(flux) if flux else 0
            min_flux = min(flux) if flux else 0
            
            entry = {
                'dash_context': {
                    'spectrum_id': {'value': spectrum.id},
                    'target_redshift': {'value': z},
                    'min-flux': {'value': min_flux},
                    'max-flux': {'value': max_flux}
                },
                'time': str(spectrum.timestamp).split('+')[0],
                'spec_extras': spec_extras,
                'spectrum': spectrum
            }
            
            html = render_to_string(
                template_name='custom_code/single_spectrum.html',
                context={'entry': entry, 'target': target},
                request=request
            )
            
            return JsonResponse({'html': html}, safe=False)
        except Exception as e:
            logger.error(f"Error loading spectrum {spectrum_id}: {e}")
            return JsonResponse({'html': f'<div>Error loading spectrum: {e}</div>'}, safe=False)
    
    return JsonResponse({'html': '<div>Error: Missing parameters</div>'}, safe=False)


def fit_lightcurve_view(request):

    target_id = request.GET.get('target_id', None)
    target = Target.objects.get(id=target_id)
    user_id = request.GET.get('user_id', None)
    user = User.objects.get(id=user_id)
    filt = request.GET.get('filter', None)
    days = float(request.GET.get('days', 20))

    fit = custom_code_tags.lightcurve_fits(target, user, filt, days)
    lightcurve_plot = fit['plot']
    fitted_max = fit['max']
    max_mag = fit['mag']
    fitted_filt = fit['filt']
    
    if fitted_max:

        fitted_date = date.strftime(Time(fitted_max, scale='utc', format='jd').datetime, "%m/%d/%Y")

        context = {
            'success': 'success',
            'lightcurve_plot': lightcurve_plot,
            'fitted_max': '{} ({})'.format(fitted_date, fitted_max),
            'max_mag': max_mag,
            'max_filt': fitted_filt
        }

    else:
        context = {
            'success': 'failure',
            'lightcurve_plot': lightcurve_plot,
            'fitted_max': fitted_max,
            'max_mag': max_mag,
            'max_filt': fitted_filt
        }

    return HttpResponse(json.dumps(context), content_type='application/json')


def save_lightcurve_params_view(request):

    target_id = request.GET.get('target_id', None)
    target = Target.objects.get(id=target_id)
    key = request.GET.get('key', None)

    if key == 'target_description':
        value = request.GET.get('value', None)
        
    else:
        datestring = request.GET.get('date', None)
        date = datestring.split()[0]
        jd = datestring.split()[1].replace('(', '').replace(')', '')
 
        value = json.dumps({'date': date,
                 'jd': jd,
                 'mag': request.GET.get('mag', None),
                 'filt': request.GET.get('filt', None),
                 'source': request.GET.get('source', None)})
    setattr(target, key, value)
    target.save()
    logger.info('Saved {} for target {}'.format(key, target_id))

    return HttpResponse(json.dumps({'success': 'Saved'}), content_type='application/json')


class ObservationGroupDetailView(DetailView):
    """
    View for displaying the details and records associated with
    an ObservationGroup object
    """
    model = ObservationGroup

    def get_queryset(self, *args, **kwargs):
        """
        Gets set of ObservationGroup objects associated with targets that
        the current user is authorized to view
        """
        #return get_objects_for_user(self.request.user, 'tom_observations.view_observationgroup')
        obsgroupids = get_objects_for_user(self.request.user, 'tom_observations.view_observationrecord').order_by('observationgroup').values_list('observationgroup', flat=True).distinct()

        return ObservationGroup.objects.filter(id__in=obsgroupids)

    def get_context_data(self, *args, **kwargs):
        """
        Adds items to context object for this view, including the associated
        observation records in ascending order of creation date
        """
        context = super().get_context_data(*args, **kwargs)
        obs_records = self.object.observation_records.all().order_by('created')
        parameters = []
        for obs in obs_records:
            p = {'start': obs.parameters['start'].replace('T', ' '),
                 'end': obs.parameters.get('end', ''),
                 'status': obs.status,
                 'obs_id': obs.observation_id,
                 'cadence': obs.parameters['cadence_frequency'],
                 'site': obs.parameters.get('site', ''),
                 'instrument': obs.parameters['instrument_type'],
                 'proposal': obs.parameters['proposal'],
                 'ipp': obs.parameters['ipp_value'],
                 'airmass': obs.parameters['max_airmass']
            }
            first_filt = []
            other_filts = []
            acq_radius = []
            if obs.parameters['observation_type'] == 'SPECTRA':
                acq_radius = obs.parameters['acquisition_radius']
                p['acq_radius'] = acq_radius
            for f in ['U', 'B', 'V', 'R', 'I', 'u', 'gp', 'rp', 'ip', 'zs', 'w']:
                if f in obs.parameters.keys() and not obs.parameters[f]:
                    continue
                elif f in obs.parameters.keys() and obs.parameters[f][0]:
                    current_filt = obs.parameters[f]
                    if not first_filt:
                        first_filt = {
                            'filt': f, 
                            'exptime': current_filt[0], 
                            'numexp': current_filt[1], 
                            'blocknum': current_filt[2]
                        }
                    else:
                        other_filts.append({
                            'filt': f, 
                            'exptime': current_filt[0], 
                            'numexp': current_filt[1], 
                            'blocknum': current_filt[2]
                        })

            p['first_filter'] = first_filt
            p['other_filters'] = other_filts
            parameters.append(p)

        context['parameters'] = parameters
        context['records'] = self.object.observation_records.all().order_by('created')
        return context


class BrokerTargetView(FilterView):
 
    template_name = 'custom_code/broker_query_targets.html'
    model = BrokerTarget
    paginate_by = 10
    context_object_name = 'brokertargets'
    strict = False
    filterset_class = BrokerTargetFilter
    ordering = ['-created']

    def get_filterset_kwargs(self, filterset_class):
        ### Initially filter so only new targets are displayed
        kwargs = super(BrokerTargetView, self).get_filterset_kwargs(filterset_class)
        if kwargs['data'] is None:
            kwargs['data'] = {'status': 'New'}
        elif 'status' not in kwargs['data']:
            kwargs['data']._mutable = True
            kwargs['data']['status'] = 'New'
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        #TNS_URL = "https://www.wis-tns.org/object/"
        for target in context['object_list']:
            target.coords = make_coords(target.ra, target.dec)
            if target.tns_target is not None:
                target.tns_name = target.tns_target.name

            targetname_matchlist = Target.objects.filter(Q(name__icontains=target.name) | Q(aliases__name__icontains=target.name)).distinct().first()
            if target.tns_target:
                target_tnsname_matchlist = Target.objects.filter(Q(name__icontains=target.tns_target.name) | Q(aliases__name__icontains=target.tns_target.name)).distinct().first()

            if targetname_matchlist:
                target.existing_target = targetname_matchlist.id
                target.exists = True
            elif target.tns_target and target_tnsname_matchlist:
                target.existing_target = target_tnsname_matchlist.id
                target.exists = True
            else:
                target.exists = False
            
        #    target.link = TNS_URL + target.name
        return context


def query_swift_observations_view(request):
   target_id = request.GET['target_id']
   t = Target.objects.get(id=target_id)
   ra, dec = t.ra, t.dec

   #from swifttools.swift_too import Swift_ObsQuery
   #username, shared_secret = os.environ['SWIFT_USERNAME'], os.environ['SWIFT_SECRET']
   #query = Swift_ObsQuery()
   #query.username = username
   #query.shared_secret = shared_secret
   #query.ra, query.dec = ra, dec
   #query.radius = 5 / 60 #5 arcmin

   #if query.submit():
   #    logger.info('Queried Swift for target {}'.format(target_id))
   #else:
   #    logger.info('Querying Swift failed with status {}'.format(query.status))
   #    content_response = {'success': 'Failed'}

   #if len(query):
   #    content_response = {'success': 'Yes'}
   #else:
   #    content_response = {'success': 'No'}

   ### NOT CURRENTLY FUNCTIONAL
   content_response = {'success': 'No'}

   return HttpResponse(json.dumps(content_response), content_type='application/json')


def make_thumbnail_view(request):

    filename_dict = json.loads(request.GET['filenamedict'])
    logger.info(f'filename_dict: {filename_dict}')
    zoom = float(request.GET['zoom'])
    sigma = float(request.GET['sigma'])

    if filename_dict['psfx'] < 9999 and filename_dict['psfy'] < 9999:
        f = make_thumb([os.path.join(settings.FITS_DIR,filename_dict['filepath'].lstrip('/'),filename_dict['filename']+'.fits')], grow=zoom, spansig=sigma, x=filename_dict['psfx'], y=filename_dict['psfy'], ticks=True)
    else:
        f = make_thumb([os.path.join(settings.FITS_DIR,filename_dict['filepath'].lstrip('/'),filename_dict['filename']+'.fits')], grow=zoom, spansig=sigma, x=1024, y=1024, ticks=False)

    with open(os.path.join(settings.THUMB_DIR,f[0]), 'rb') as imagefile:
        b64_image = base64.b64encode(imagefile.read())
        thumb = b64_image.decode('utf-8')

    content_response = {'success': 'Yes',
                        'thumb': 'data:image/png;base64,{}'.format(thumb),
                        'telescope': filename_dict['tele'],
                        'instrument': filename_dict['instr'],
                        'filter': filename_dict['filter'],
                        'exptime': filename_dict['exptime']
                    }

    return HttpResponse(json.dumps(content_response), content_type='application/json')

def download_fits_view(request):
    token = settings.FACILITIES['LCO']['api_key']
    url = settings.FACILITIES['LCO']['archive_url']
    
    object_basename = json.loads(request.GET.get('filename'))['filename']

    results = requests.get(url,
                           headers={'Authorization': f'Token {token}'}, 
                           params={'basename_exact': object_basename, 'include_related_frames': False}).json()["results"]
    
    data = requests.get(results[0]["url"]).content

    return FileResponse(BytesIO(data),filename=object_basename+'.fits', as_attachment=True)
    
class InterestingTargetsView(ListView):

    template_name = 'custom_code/interesting_targets.html'
    model = Target
    context_object_name = 'global_interesting_targets'

    def get_queryset(self):
        interesting_targets_list = TargetList.objects.filter(name='Interesting Targets').first()
        if interesting_targets_list:
            global_interesting_targets = interesting_targets_list.targets.all()
            logger.info('Got list of global interesting targets')
            return global_interesting_targets
        else:
            return []

    def get_context_data(self, **kwargs):
        context = super(InterestingTargetsView, self).get_context_data(**kwargs)
        active_cadences = DynamicCadence.objects.filter(active=True)
        active_target_ids = [c.observation_group.observation_records.first().target.id for c in active_cadences]
        for target in context['global_interesting_targets']:
            target.best_name = custom_code_tags.get_best_name(target)
            target.classification = Target.objects.get(pk=target.pk).classification
            target.redshift = Target.objects.get(pk=target.pk).redshift
            target.description = Target.objects.get(pk=target.pk).target_description
            target.science_tags = ', '.join([s.tag for s in ScienceTags.objects.filter(id__in=[t.tag_id for t in TargetTags.objects.filter(target_id=target.id)])])
            if target.id in active_target_ids:
                target.active_cadences = 'Yes'
            else:
                target.active_cadences = 'No'
        logger.info('Finished getting context data for global interesting targets')

        context['personal_interesting_targets'] = [q.target for q in InterestedPersons.objects.filter(user=self.request.user)] 
        for target in context['personal_interesting_targets']:
            target.best_name = custom_code_tags.get_best_name(target)
            target.classification = Target.objects.get(pk=target.pk).classification
            target.redshift = Target.objects.get(pk=target.pk).redshift
            target.description = Target.objects.get(pk=target.pk).target_description
            target.science_tags = ', '.join([s.tag for s in ScienceTags.objects.filter(id__in=[t.tag_id for t in TargetTags.objects.filter(target_id=target.id)])])
            if target.id in active_target_ids:
                target.active_cadences = 'Yes'
            else:
                target.active_cadences = 'No'
        logger.info('Finished getting context data for personal interesting targets')
        context['interesting_group_id'] = TargetList.objects.get(name='Interesting Targets').id
        return context


def sync_targetextra_view(request):
    newdata = json.loads(request.GET.get('newdata'))
    target_id = newdata.get('targetid')
    target = Target.objects.get(id=target_id)
    if newdata['key'] == 'classification':
        target.classification = newdata['value']
    elif newdata['key'] == 'redshift':
        newz = newdata['value']
        if newdata['value'] == '':
            newz = None
        target.redshift = newz
    elif newdata['key'] == 'name':
        print('When updating alias,the target.save() just needs to happen')
    logger.info(f"Updated target {newdata['key']} to {newdata['value']}")
    target.save()

    run_hook('targetextra_post_save',target)
    
    return HttpResponse(json.dumps({'success': 'Synced'}), content_type='application/json')


@receiver(comment_was_posted)
def target_comment_receiver(sender, **kwargs):
    posted_comment = kwargs['comment']
    comment = posted_comment.comment
    content_type = ContentType.objects.get(id=posted_comment.content_type_id).model
    logger.info(f'new comment that needs to be synced: {comment}')
    logger.info(f'content_type: {content_type}')
    logger.info(f'posted_comment: {posted_comment}')
    if content_type == 'target':
        tablename = 'targets'
        target_id = int(posted_comment.object_pk)
        user_id = int(posted_comment.user_id)
        logger.info(f'why isnt the sync comments running? {run_hook("sync_comment_with_snex1", comment, tablename, user_id, target_id, target_id)}')
        if not settings.DEBUG:
            run_hook('sync_comment_with_snex1', comment, tablename, user_id, target_id, target_id)


def change_broker_target_status_view(request):
    
    try:
        target_id = request.GET.get('target_id', '')
        brokertarget = BrokerTarget.objects.get(id=target_id)
        new_status = request.GET.get('new_status')
        brokertarget.status = new_status
        brokertarget.save()

        context = {'update': 'Success'}
    
    except:
        context = {'update': 'Failed'} 
    
    return HttpResponse(json.dumps(context), content_type='application/json')


class SNEx2SpectroscopyTNSSharePassthrough(RedirectView):

    def get_redirect_url(self, *args, **kwargs):
        target_id = kwargs['pk']
        datum_id = kwargs['datum_pk']
        print(f"Redirecting to share for target {target_id} and reduced datum {datum_id}")
        # We need to check if the datum has an associated dataproduct here, and if it does not, we should create it and add it to the TOM
        datum = ReducedDatum.objects.get(pk=datum_id)
        if not datum.data_product:
            print(f"Reduced datum {datum_id} does not have an associated data product - creating it now")
            target = Target.objects.get(pk=target_id)
            data_str = ''
            for datapoint in datum.value.values():
                data_str += f"{datapoint.get('wavelength')}\t{datapoint.get('flux')}\n"
            dp_name = f"spectra_{datum_id}_{datum.timestamp.strftime('%Y_%m_%d_%H_%M_%S')}.txt"
            dp = DataProduct.objects.create(target=target, product_id=dp_name, data_product_type='spectroscopy')
            dp.data.save(dp_name, ContentFile(data_str))
            ReducedDatum.objects.filter(pk=datum_id).update(data_product=dp)
        return reverse('tns:report-tns', kwargs={'pk': target_id, 'datum_pk': datum_id})


class FloydsInboxView(TemplateView):

    template_name = 'custom_code/floyds_inbox.html'

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        targetids, propids, dateobs, paths, filenames, imgpaths = get_unreduced_spectra()

        inbox_rows = []
        for i in range(len(targetids)):
            current_dict = {}
            t = Target.objects.get(id=targetids[i])
            current_dict['targetid'] = targetids[i]
            current_dict['targetnames'] = custom_code_tags.smart_name_list(t)
            current_dict['propid'] = propids[i]
            current_dict['dateobs'] = dateobs[i]
            current_dict['path'] = paths[i]
            current_dict['filename'] = filenames[i]
            
            with open(imgpaths[i], 'rb') as imagefile:
                b64_image = base64.b64encode(imagefile.read())
                thumb = b64_image.decode('utf-8')
            current_dict['img'] = 'data:image/png;base64,{}'.format(thumb) 
            
            inbox_rows.append(current_dict)

        context['inbox_rows'] = inbox_rows

        return context


class AuthorshipInformation(TemplateView):

    template_name = 'custom_code/authorship.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context


def download_photometry_view(request, targetid):

    user = request.user
    target = Target.objects.get(id=int(targetid))

    if settings.TARGET_PERMISSIONS_ONLY:
        datums = ReducedDatum.objects.filter(target=target, data_type=settings.DATA_PRODUCT_TYPES['photometry'][0])

    else:
        datums = get_objects_for_user(user,
                                      'tom_dataproducts.view_reduceddatum',
                                      klass=ReducedDatum.objects.filter(
                                        target=target,
                                        data_type=settings.DATA_PRODUCT_TYPES['photometry'][0]))

    datums = datums.order_by('timestamp')
    newfile = StringIO()

    newfile.write('mjd mag err filter subtracted?\n')

    for d in datums:
        if all(k in d.value.keys() for k in ['magnitude', 'error', 'filter']):
            newfile.write('{} {} {} {} {}\n'.format(round(Time(d.timestamp).mjd, 2), d.value['magnitude'], d.value['error'], d.value['filter'], d.value.get('background_subtracted', False)))

    response = HttpResponse(newfile.getvalue(), content_type='text/plain')
    response['Content-Disposition'] = 'attachment; filename={}.txt'.format(target.name.replace(' ',''))
    return response


def get_target_standards_view(request):

    target_id = request.GET.get('target_id', '')

    standard_info = get_standards_from_snex1(target_id)
    
    html = render_to_string(
        template_name='custom_code/partials/get_target_standards.html',
        context={'standards': standard_info}
    )

    data_dict = {"html_from_view": html}

    return JsonResponse(data=data_dict, safe=False)


class TargetFilterForm(forms.Form):
    apply_name_filter        = forms.BooleanField(required=False)
    target_name              = forms.CharField(required=False, widget=forms.TextInput(attrs={'placeholder': 'Name contains'}))
    apply_ra_filter          = forms.BooleanField(required=False)
    min_ra                   = forms.FloatField(required=False, widget=forms.NumberInput(attrs={'placeholder': 'Min RA'}))
    max_ra                   = forms.FloatField(required=False, widget=forms.NumberInput(attrs={'placeholder': 'Max RA'}))
    apply_dec_filter         = forms.BooleanField(required=False)
    min_dec                  = forms.FloatField(required=False, widget=forms.NumberInput(attrs={'placeholder': 'Min Dec'}))
    max_dec                  = forms.FloatField(required=False, widget=forms.NumberInput(attrs={'placeholder': 'Max Dec'}))
    apply_class_filter       = forms.BooleanField(required=False)
    class_name               = forms.CharField(required=False, widget=forms.TextInput(attrs={'placeholder': 'Class contains'}))
    apply_class_exclude_filter = forms.BooleanField(required=False)
    class_exclude_name       = forms.CharField(required=False, widget=forms.TextInput(attrs={'placeholder': 'Exclude class'}))
    apply_redshift_filter    = forms.BooleanField(required=False)
    min_red                  = forms.FloatField(required=False, widget=forms.NumberInput(attrs={'placeholder': 'Min z'}))
    max_red                  = forms.FloatField(required=False, widget=forms.NumberInput(attrs={'placeholder': 'Max z'}))
    apply_date_created_filter = forms.BooleanField(required=False)
    date_created_min = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'placeholder': 'Min date'
            }
        ),
        input_formats=['%Y-%m-%d'],
    )
    date_created_max = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'placeholder': 'Max date'
            }
        ),
        input_formats=['%Y-%m-%d'],
    )
    apply_photometry_count_filter = forms.BooleanField(required=False)
    min_photometry_points = forms.IntegerField(
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={'placeholder': '≥ phot pts'})
    )
    apply_spectra_count_filter = forms.BooleanField(required=False)
    min_spectra_points = forms.IntegerField(
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={'placeholder': '≥ spectra'})
    )
    apply_recent_obs_filter = forms.BooleanField(required=False)
    recent_obs_kind = forms.ChoiceField(
        required=False,
        choices=[('any', 'Any'), ('phot', 'Photometry'), ('spec', 'Spectroscopy')],
        widget=forms.Select()
    )
    recent_obs_days = forms.IntegerField(
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={'placeholder': 'in last N days'})
    )
    apply_recent_date_filter = forms.BooleanField(required=False)
    recent_date_kind = forms.ChoiceField(
        required=False,
        choices=[('any', 'Any'), ('phot', 'Photometry'), ('spec', 'Spectroscopy')],
        widget=forms.Select()
    )
    recent_date_threshold = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'placeholder': 'On or after'}),
        input_formats=['%Y-%m-%d'],
    )
    apply_recent_date_before_filter = forms.BooleanField(required=False)
    recent_date_before_kind = forms.ChoiceField(
        required=False,
        choices=[('any', 'Any'), ('phot', 'Photometry'), ('spec', 'Spectroscopy')],
        widget=forms.Select()
    )
    recent_date_before_threshold = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'placeholder': 'On or before'}),
        input_formats=['%Y-%m-%d'],
    )
    apply_mag_bright_filter = forms.BooleanField(required=False)
    mag_bright_mode = forms.ChoiceField(
        required=False,
        choices=[('any', 'Any obs'), ('last', 'Last obs')],
        widget=forms.Select()
    )
    mag_bright_threshold = forms.FloatField(
        required=False,
        widget=forms.NumberInput(attrs={'placeholder': 'mag < X (brighter)'}),
        min_value=-30  # sane guard; tweak as you like
    )
    apply_proposal_filter = forms.BooleanField(required=False)
    proposal_choice = forms.ChoiceField(required=False, choices=[], widget=forms.Select())


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_show_labels = False

        codes = (
            ObservationRecord.objects
            .filter(parameters__has_key='proposal')
            .values_list('parameters__proposal', flat=True)
            .distinct()
            .order_by('parameters__proposal')
        )

        self.fields['proposal_choice'].choices = [('', '— Select proposal —')] + [(c, c) for c in codes][::-1]


        self.helper.layout = Layout(
            HTML('<h3 class="mb-3">Search Filters '
                 '<small class="text-muted">(optional)</small></h3>'),
            Div(
                Row(
                    Column(
                        # Column 1
                        Row(
                            Column('apply_name_filter', css_class='col-auto'),
                            Column('target_name',         css_class='col'),
                            css_class='form-row align-items-center mb-2'
                        ),
                        Row(
                            Column('apply_ra_filter', css_class='col-auto'),
                            Column('min_ra',           css_class='col'),
                            Column('max_ra',           css_class='col'),
                            css_class='form-row align-items-center mb-2'
                        ),
                        Row(
                            Column('apply_mag_bright_filter', css_class='col-auto'),
                            Column('mag_bright_mode',         css_class='col-4'),
                            HTML('<div class="col-auto text-center align-self-center">&lt;</div>'),
                            Column('mag_bright_threshold',    css_class='col'),
                            css_class='form-row align-items-center'
                        ),
                        Row(
                            Column('apply_proposal_filter', css_class='col-auto'),
                            Column('proposal_choice',       css_class='col'),
                            css_class='form-row align-items-center'
                        ),

                        css_class='col-lg-4 col-md-6 mb-3'
                    ),


                    Column(
                        # Column 2
                        Row(
                            Column('apply_dec_filter', css_class='col-auto'),
                            Column('min_dec',          css_class='col'),
                            Column('max_dec',          css_class='col'),
                            css_class='form-row align-items-center mb-2'
                        ),
                        Row(
                            Column('apply_class_filter', css_class='col-auto'),
                            Column('class_name',         css_class='col'),
                            css_class='form-row align-items-center mb-2'
                        ),
                        Row(
                            Column('apply_class_exclude_filter', css_class='col-auto'),
                            Column('class_exclude_name',         css_class='col'),
                            css_class='form-row align-items-center'
                        ),
                        Row( 
                            Column('apply_spectra_count_filter', css_class='col-auto'),
                            Column('min_spectra_points',         css_class='col'),
                            css_class='form-row align-items-center'
                        ),
                        css_class='col-lg-4 col-md-6 mb-3'
                    ),
                    Column(
                        # Column 3
                        Row(
                            Column('apply_redshift_filter', css_class='col-auto'),
                            Column('min_red',               css_class='col'),
                            Column('max_red',               css_class='col'),
                            css_class='form-row align-items-center mb-2'
                        ),
                        Row(
                            Column('apply_date_created_filter', css_class='col-auto'),
                            Column('date_created_min',          css_class='col'),
                            Column('date_created_max',          css_class='col'),
                            css_class='form-row align-items-center'
                        ),
                        Row(
                            Column('apply_photometry_count_filter', css_class='col-auto'),
                            Column('min_photometry_points',         css_class='col'),
                            css_class='form-row align-items-center mb-2'
                        ),
                        Row(
                            Column('apply_recent_date_filter', css_class='col-auto'),
                            Column('recent_date_kind',         css_class='col-4'),
                            HTML('<div class="col-auto text-center align-self-center">≥</div>'),
                            Column('recent_date_threshold',    css_class='col'),
                            css_class='form-row align-items-center'
                        ),
                        Row(
                            Column('apply_recent_date_before_filter', css_class='col-auto'),
                            Column('recent_date_before_kind',         css_class='col-4'),
                            HTML('<div class="col-auto text-center align-self-center">≤</div>'),
                            Column('recent_date_before_threshold',    css_class='col'),
                            css_class='form-row align-items-center'
                        ),

                        Row(
                            Column('apply_recent_obs_filter', css_class='col-auto'),
                            Column('recent_obs_kind',         css_class='col-4'),
                            Column('recent_obs_days',         css_class='col'),
                            css_class='form-row align-items-center'
                        ),
                        css_class='col-lg-4 col-md-6 mb-3'
                    ),
                    css_class='gx-3'
                ),
                css_class='bg-light p-4 rounded'
            ),
            Div(
                Submit('submit', 'Submit', css_class='btn btn-outline-light btn-lg'),
                css_class='text-right mt-4'
            )
        )

class TargetFilteringView(FormView):
    template_name = 'custom_code/target_filter.html'
    form_class    = TargetFilterForm
    success_url   = reverse_lazy('custom_code:target_filter')

    def form_valid(self, form):
        cd = form.cleaned_data
        filters = Q()

        # Use SNExTarget since redshift and classification are direct fields on it
        photometry_q = Q(reduceddatum__data_type='photometry') & Q(reduceddatum__value__has_key='magnitude')
        spectroscopy_q = Q(reduceddatum__data_type='spectroscopy')

        # Start with only targets the user has permission to view
        if self.request.user.is_authenticated:
            qs = get_objects_for_user(
                self.request.user,
                'tom_targets.view_target',
                accept_global_perms=True
            ).annotate(
                phot_count=Count('reduceddatum', filter=photometry_q, distinct=True),
                spectra_count=Count('reduceddatum', filter=spectroscopy_q, distinct=True),  
            )
        else:
            # Anonymous users get empty queryset
            qs = Target.objects.none().annotate(
                phot_count=Count('reduceddatum', filter=photometry_q, distinct=True),
                spectra_count=Count('reduceddatum', filter=spectroscopy_q, distinct=True),  
            )

        # name filter
        if cd.get('apply_name_filter'):
            name = cd.get('target_name','').strip()
            if name:
                name_q = (
                    Q(name__icontains=name)
                  | Q(aliases__name__icontains=name)
                  | Q(name__icontains=name.lower().replace('SN ',''))
                  | Q(aliases__name__icontains=name.lower().replace('SN ',''))
                )
                filters &= name_q


        # RA filter
        if cd.get('apply_ra_filter'):
            h_min = cd.get('min_ra'); h_max = cd.get('max_ra')
            if h_min is not None:
                filters &= Q(ra__gte=h_min * 15.0)
            if h_max is not None:
                filters &= Q(ra__lte=h_max * 15.0)
        
        # dec filter
        if cd.get('apply_dec_filter'):
            d_min = cd.get('min_dec'); d_max = cd.get('max_dec')
            if d_min is not None:
                filters &= Q(dec__gte=d_min)
            if d_max is not None:
                filters &= Q(dec__lte=d_max)

        # Date-created filter
        if cd.get('apply_date_created_filter'):
            min_date = cd.get('date_created_min')    # this is already a date or None
            if min_date:
                qs = qs.filter(created__date__gte=min_date)
            max_date = cd.get('date_created_max')
            if max_date:
                qs = qs.filter(created__date__lte=max_date)
                
        # classification contains
        if cd.get('apply_class_filter'):
            cls = cd.get('class_name','').strip()
            if cls:
                filters &= Q(snextarget__classification__icontains=cls)

        # Classification doesn't contain
        if cd.get('apply_class_exclude_filter'):
            excl = cd.get('class_exclude_name','').strip()
            if excl:
                filters &= ~Q(snextarget__classification__icontains=excl)

        # Redshift range
        if cd.get('apply_redshift_filter'):
            min_z = cd.get('min_red'); max_z = cd.get('max_red')
            if min_z is not None:
                filters &= Q(snextarget__redshift__gte=min_z)
            if max_z is not None:
                filters &= Q(snextarget__redshift__lte=max_z)

        # Photometry count threshold
        if cd.get('apply_photometry_count_filter'):
            nmin = cd.get('min_photometry_points')
            if nmin is not None:
                filters &= Q(phot_count__gte=nmin)

        if cd.get('apply_spectra_count_filter'):
            nmin = cd.get('min_spectra_points')
            if nmin is not None:
                filters &= Q(spectra_count__gte=nmin)

        # --- Recent observations in last N days (Any/Phot/Spec)
        if cd.get('apply_recent_obs_filter'):
            kind = (cd.get('recent_obs_kind') or 'any').lower()
            ndays = cd.get('recent_obs_days')

            if ndays:
                cutoff = timezone.now() - timedelta(days=ndays)

                # Build dynamic kwarg for timestamp field
                ts_kw = {f'timestamp__gte': cutoff}

                # Subqueries for Exists(...)
                recent_any_sq = ReducedDatum.objects.filter(
                    target=OuterRef('pk'),
                    **ts_kw
                )
                recent_phot_sq = ReducedDatum.objects.filter(
                    target=OuterRef('pk'),
                    data_type='photometry',
                    **ts_kw
                ).filter(value__has_key='magnitude')  # keep consistent with your photometry count
                recent_spec_sq = ReducedDatum.objects.filter(
                    target=OuterRef('pk'),
                    data_type='spectroscopy',
                    **ts_kw
                )

                # Annotate booleans once; then filter on them
                qs = qs.annotate(
                    has_recent_any = Exists(recent_any_sq),
                    has_recent_phot = Exists(recent_phot_sq),
                    has_recent_spec = Exists(recent_spec_sq),
                )

                if kind == 'any':
                    filters &= Q(has_recent_any=True)
                elif kind == 'phot':
                    filters &= Q(has_recent_phot=True)
                elif kind == 'spec':
                    filters &= Q(has_recent_spec=True)

        # --- On/after (≥) ---
        if cd.get('apply_recent_date_filter'):
            kind = (cd.get('recent_date_kind') or 'any').lower()
            date_cut = cd.get('recent_date_threshold')
            if date_cut:
                recent_any_sq = ReducedDatum.objects.filter(
                    target=OuterRef('pk'),
                    timestamp__date__gte=date_cut,
                )
                recent_phot_sq = ReducedDatum.objects.filter(
                    target=OuterRef('pk'),
                    data_type='photometry',
                    timestamp__date__gte=date_cut,
                ).filter(value__has_key='magnitude')
                recent_spec_sq = ReducedDatum.objects.filter(
                    target=OuterRef('pk'),
                    data_type='spectroscopy',
                    timestamp__date__gte=date_cut,
                )

                qs = qs.annotate(
                    has_since_any  = Exists(recent_any_sq),
                    has_since_phot = Exists(recent_phot_sq),
                    has_since_spec = Exists(recent_spec_sq),
                )
                filters &= {
                    'any':  Q(has_since_any=True),
                    'phot': Q(has_since_phot=True),
                    'spec': Q(has_since_spec=True),
                }[kind]

        # --- On/before (≤) ---
        if cd.get('apply_recent_date_before_filter'):
            kind = (cd.get('recent_date_before_kind') or 'any').lower()
            date_cut = cd.get('recent_date_before_threshold')
            if date_cut:
                recent_any_sq = ReducedDatum.objects.filter(
                    target=OuterRef('pk'),
                    timestamp__date__lte=date_cut,
                )
                recent_phot_sq = ReducedDatum.objects.filter(
                    target=OuterRef('pk'),
                    data_type='photometry',
                    timestamp__date__lte=date_cut,
                ).filter(value__has_key='magnitude')
                recent_spec_sq = ReducedDatum.objects.filter(
                    target=OuterRef('pk'),
                    data_type='spectroscopy',
                    timestamp__date__lte=date_cut,
                )

                qs = qs.annotate(
                    has_before_any  = Exists(recent_any_sq),
                    has_before_phot = Exists(recent_phot_sq),
                    has_before_spec = Exists(recent_spec_sq),
                )
                filters &= {
                    'any':  Q(has_before_any=True),
                    'phot': Q(has_before_phot=True),
                    'spec': Q(has_before_spec=True),
                }[kind]


        # --- Any/Last observation with apparent mag brighter than X (any filter)
        ## TODO: this is very slow for some reason
        if cd.get('apply_mag_bright_filter'):
            mode = (cd.get('mag_bright_mode') or 'any').lower()
            X = cd.get('mag_bright_threshold')

            if X is not None:
                # Base photometry queryset: has numeric magnitude in JSON
                base_phot = ReducedDatum.objects.filter(
                    target=OuterRef('pk'),
                    data_type='photometry',
                    value__has_key='magnitude',
                )

                # ANY obs: fast Exists()
                if mode == 'any':
                    any_bright_sq = base_phot.annotate(
                        mag_txt=KeyTextTransform('magnitude', F('value')),
                        mag=Cast('mag_txt', FloatField()),
                    ).filter(mag__lt=X)

                    qs = qs.annotate(has_any_bright=Exists(any_bright_sq))
                    filters &= Q(has_any_bright=True)

                # LAST obs: get latest row id, then its mag
                else:
                    latest_phot_id_sq = base_phot.order_by('-timestamp').values('pk')[:1]

                    qs = qs.annotate(
                        latest_phot_id=Subquery(latest_phot_id_sq)
                    )

                    last_mag_sq = ReducedDatum.objects.filter(
                        pk=OuterRef('latest_phot_id')
                    ).annotate(
                        mag_txt=KeyTextTransform('magnitude', F('value')),
                        mag=Cast('mag_txt', FloatField()),
                    ).values('mag')[:1]

                    qs = qs.annotate(last_mag=Subquery(last_mag_sq))
                    filters &= Q(last_mag__lt=X)


        if cd.get('apply_proposal_filter'):
            code = cd.get('proposal_choice')
            if code:
                base = ObservationRecord.objects.filter(
                    target=OuterRef('pk'),
                    parameters__proposal=code
                )
                qs = qs.annotate(has_proposal=Exists(base))
                filters &= Q(has_proposal=True)

        # apply query
        target_match_list = qs.filter(filters).distinct()

        # compute totals for photometry and spectra across all matched targets
        tot_phot = target_match_list.aggregate(total=Sum('phot_count'))['total'] or 0
        tot_spec = target_match_list.aggregate(total=Sum('spectra_count'))['total'] or 0


        # return result
        return self.render_to_response(
            self.get_context_data(
                form=form,
                targets=target_match_list,
                tot_phot=tot_phot,
                tot_spec=tot_spec,
            )
        )


    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('targets', [])
        return context
