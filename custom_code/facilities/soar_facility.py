import copy
import json
import logging
from datetime import timedelta, datetime

import requests
from crispy_forms.bootstrap import PrependedText
from crispy_forms.layout import Column, Div, HTML, Layout, Row
from django import forms
from django.conf import settings
from tom_common.exceptions import ImproperCredentialsException
from tom_observations.facilities.soar import (
    SOARFacility as BaseSOARFacility,
    SOARSpectroscopyObservationForm,
)
from tom_targets.models import Target


try:
    SOAR_SETTINGS = settings.FACILITIES['SOAR']
except (AttributeError, KeyError):
    SOAR_SETTINGS = {
        'portal_url': 'https://observe.lco.global',
        'api_key': '',
        'access_group_name': 'PASSTA',
    }

PORTAL_URL = SOAR_SETTINGS['portal_url']
TERMINAL_OBSERVING_STATES = ['COMPLETED', 'CANCELED', 'WINDOW_EXPIRED']
SOAR_GROUP_NAME = SOAR_SETTINGS.get('access_group_name', 'PASSTA')
logger = logging.getLogger(__name__)


def make_request(*args, **kwargs):
    response = requests.request(*args, **kwargs)
    if 400 <= response.status_code < 500:
        raise ImproperCredentialsException('SOAR: ' + str(response.content))
    response.raise_for_status()
    return response


def log_payload(action, payload):
    logger.warning(
        'SOAR %s payload:\n%s',
        action,
        json.dumps(payload, indent=2, sort_keys=True, default=str)
    )


def user_can_access_soar(user):
    return bool(
        getattr(user, 'is_authenticated', False)
        and user.groups.filter(name=SOAR_GROUP_NAME).exists()
    )


class SOARObservationForm(SOARSpectroscopyObservationForm):
    max_airmass = forms.FloatField(initial=1.6, min_value=1.0, max_value=5.0, required=False, label='')
    min_lunar_distance = forms.IntegerField(initial=20, min_value=0, required=False, label='')
    rotator_angle = forms.FloatField(initial=0.0, required=False, widget=forms.HiddenInput())
    exposure_count = forms.IntegerField(
        initial=2,
        min_value=1,
        required=False,
        label='',
        widget=forms.NumberInput(attrs={'min': 1})
    )
    exposure_time = forms.FloatField(
        min_value=0.1,
        label='',
        widget=forms.TextInput(attrs={'placeholder': 'Seconds'})
    )
    observation_mode = forms.ChoiceField(
        choices=(('NORMAL', 'Normal'), ('TARGET_OF_OPPORTUNITY', 'Rapid Response')),
        initial='NORMAL',
        required=False,
        widget=forms.HiddenInput()
    )
    grating = forms.CharField(required=False, widget=forms.HiddenInput())
    filter = forms.CharField(required=False, widget=forms.HiddenInput())
    readout = forms.ChoiceField(choices=(), required=False, label='')
    name = forms.CharField(required=False, widget=forms.HiddenInput())

    INSTRUMENT_DEFAULTS = {
        'SOAR_GHTS_REDCAM': {
            'exposure_time': 450,
            'max_airmass': 1.6,
            'exposure_count': 2,
            'readout': 'GHTS_R_400m2_2x2',
            'rotator_angle': 0,
        },
        'SOAR_GHTS_BLUECAM': {
            'exposure_time': 450,
            'max_airmass': 1.6,
            'exposure_count': 2,
            'readout': 'GHTS_B_400m1_2x2',
            'rotator_angle': 0,
        },
        'SOAR_TRIPLESPEC': {
            'exposure_time': 200,
            'max_airmass': 1.6,
            'exposure_count': 6,
            'readout': 'fowler16_coadds1',
            'rotator_angle': 90,
        },
    }

    READOUT_CHOICES_BY_INSTRUMENT = {
        'SOAR_GHTS_REDCAM': [
            ('GHTS_R_400m1_2x2', 'GHTS_R_400m1_2x2'),
            ('GHTS_R_400m2_2x2', 'GHTS_R_400m2_2x2'),
        ],
        'SOAR_GHTS_BLUECAM': [
            ('GHTS_B_400m1_2x2', 'GHTS_B_400m1_2x2'),
            ('GHTS_B_400m1_2x2_slit1p5', 'GHTS_B_400m1_2x2_slit1p5'),
        ],
        'SOAR_TRIPLESPEC': [
            ('fowler16_coadds1', 'fowler16_coadds1'),
        ],
    }

    HIDDEN_FIELDS = (
        'name',
        'facility',
        'target_id',
        'observation_type',
        'rotator_angle',
        'observation_mode',
        'grating',
        'filter',
    )

    PRIMARY_TO_ALIAS_FIELDS = {
        'instrument_type': ('c_1_instrument_type',),
        'exposure_time': ('c_1_ic_1_exposure_time',),
        'exposure_count': ('c_1_ic_1_exposure_count',),
        'readout': ('c_1_ic_1_mode',),
        'rotator_angle': ('c_1_ic_1_rotator_angle',),
        'max_airmass': ('c_1_max_airmass',),
        'min_lunar_distance': ('c_1_min_lunar_distance',),
    }

    def instrument_choices(self):
        return [
            (code, instrument["name"])
            for code, instrument in self.get_instruments().items()
        ]

    @staticmethod
    def _prepare_form_kwargs(kwargs):
        data = kwargs.get('data')
        if data is not None and hasattr(data, 'copy'):
            kwargs = kwargs.copy()
            copied_data = data.copy()
            for field_name in (
                'facility',
                'target_id',
                'observation_type',
                'name',
                'instrument_type',
                'exposure_time',
                'ipp_value',
                'proposal',
                'observation_mode',
                'start',
                'end',
            ):
                if hasattr(copied_data, 'getlist'):
                    values = [value for value in copied_data.getlist(field_name) if value not in (None, '')]
                    if values:
                        copied_data.setlist(field_name, [values[-1]])
            SOARObservationForm._synchronize_primary_and_alias_data(copied_data)
            kwargs['data'] = copied_data
        return kwargs

    @classmethod
    def _synchronize_primary_and_alias_data(cls, data):
        if not hasattr(data, 'getlist'):
            return data

        for primary_name, alias_names in cls.PRIMARY_TO_ALIAS_FIELDS.items():
            primary_values = [value for value in data.getlist(primary_name) if value not in (None, '')]
            alias_values = []
            for alias_name in alias_names:
                alias_values.extend(
                    [value for value in data.getlist(alias_name) if value not in (None, '')]
                )

            canonical_value = None
            if primary_values:
                canonical_value = primary_values[-1]
            elif alias_values:
                canonical_value = alias_values[-1]

            if canonical_value in (None, ''):
                continue

            data.setlist(primary_name, [canonical_value])
            for alias_name in alias_names:
                data.setlist(alias_name, [canonical_value])

        logger.warning(
            'SOAR bound values: instrument_type=%s alias_instrument_type=%s exposure_time=%s alias_exposure_time=%s',
            data.getlist('instrument_type'),
            data.getlist('c_1_instrument_type'),
            data.getlist('exposure_time'),
            data.getlist('c_1_ic_1_exposure_time')
        )
        return data

    def _field_value(self, name, aliases=(), default=None, include_initial=True):
        candidate_keys = (name, *aliases)
        sources = []

        if hasattr(self, 'cleaned_data'):
            sources.append(self.cleaned_data)
        if hasattr(self, 'data'):
            sources.append(self.data)
        if hasattr(self, 'initial'):
            sources.append(self.initial)

        for source in sources:
            for key in candidate_keys:
                if hasattr(source, 'get'):
                    value = source.get(key)
                    if value not in (None, ''):
                        field = self.fields.get(name)
                        if field is not None and not isinstance(value, (list, tuple, dict)):
                            try:
                                return field.to_python(value)
                            except Exception:
                                return value
                        return value

        if include_initial:
            field = self.fields.get(name)
            if field is not None and field.initial not in (None, ''):
                return field.initial

        return default

    def _selected_instrument_type(self):
        return self._field_value(
            'instrument_type',
            aliases=('c_1_instrument_type',),
            default='SOAR_GHTS_REDCAM'
        )

    def _instrument_defaults(self):
        return self.INSTRUMENT_DEFAULTS.get(
            self._selected_instrument_type(),
            self.INSTRUMENT_DEFAULTS['SOAR_GHTS_REDCAM']
        )

    def _selected_exposure_time(self):
        return self._field_value(
            'exposure_time',
            aliases=('c_1_ic_1_exposure_time',),
            default=self._instrument_defaults()['exposure_time'],
            include_initial=False
        )

    def _selected_exposure_count(self):
        return self._field_value(
            'exposure_count',
            aliases=('c_1_ic_1_exposure_count',),
            default=self._instrument_defaults()['exposure_count']
        )

    def _selected_readout(self):
        return self._field_value(
            'readout',
            aliases=('c_1_ic_1_mode',),
            default=self._instrument_defaults()['readout']
        )

    @classmethod
    def readout_choices(cls):
        seen = set()
        choices = []

        for instrument in cls.READOUT_CHOICES_BY_INSTRUMENT.values():
            for choice in instrument:
                if choice[0] not in seen:
                    choices.append(choice)
                    seen.add(choice[0])

        return choices

    def _selected_rotator_angle(self):
        return self._field_value(
            'rotator_angle',
            aliases=('c_1_ic_1_rotator_angle',),
            default=self._instrument_defaults()['rotator_angle']
        )

    def _selected_max_airmass(self):
        return self._field_value(
            'max_airmass',
            aliases=('c_1_max_airmass',),
            default=self._instrument_defaults()['max_airmass']
        )

    def _selected_min_lunar_distance(self):
        return self._field_value(
            'min_lunar_distance',
            aliases=('c_1_min_lunar_distance',),
            default=20
        )

    def clean(self):
        cleaned_data = super().clean()
        target_id = cleaned_data.get('target_id')
        instrument_type = cleaned_data.get('instrument_type') or self._selected_instrument_type()
        defaults = self.INSTRUMENT_DEFAULTS.get(instrument_type, self.INSTRUMENT_DEFAULTS['SOAR_GHTS_REDCAM'])

        if target_id:
            try:
                target = Target.objects.get(pk=target_id)
                cleaned_data['name'] = target.name
            except Target.DoesNotExist:
                self.add_error(None, 'Selected target no longer exists.')
                return cleaned_data

        max_airmass = cleaned_data.get('max_airmass')
        min_lunar_distance = cleaned_data.get('min_lunar_distance')
        exposure_time = cleaned_data.get('exposure_time')
        exposure_count = cleaned_data.get('exposure_count')
        readout = cleaned_data.get('readout')

        cleaned_data['observation_mode'] = 'NORMAL'
        cleaned_data['max_airmass'] = defaults['max_airmass'] if max_airmass in (None, '') else max_airmass
        cleaned_data['min_lunar_distance'] = 20 if min_lunar_distance in (None, '') else min_lunar_distance
        cleaned_data['rotator_angle'] = defaults['rotator_angle']
        cleaned_data['exposure_time'] = defaults['exposure_time'] if exposure_time in (None, '') else exposure_time
        cleaned_data['exposure_count'] = defaults['exposure_count'] if exposure_count in (None, '') else exposure_count
        allowed_readouts = {choice[0] for choice in self.READOUT_CHOICES_BY_INSTRUMENT.get(instrument_type, [])}
        cleaned_data['readout'] = readout if readout in allowed_readouts else defaults['readout']
        cleaned_data['grating'] = ''
        cleaned_data['filter'] = ''
        logger.info(f'instrument Bcam? {self.get_instruments()["SOAR_GHTS_BLUECAM"]}')
        return cleaned_data

    def _constraints(self):
        return {
            'max_airmass': self._selected_max_airmass(),
            'min_lunar_distance': self._selected_min_lunar_distance(),
            'max_lunar_phase': 1.0,
            'max_seeing': None,
            'min_transparency': None,
            'extra_params': {},
        }

    def _guiding_config(self):
        return {
            'optional': False,
            'mode': 'ON',
            'optical_elements': {},
            'exposure_time': None,
            'extra_params': {},
        }

    def _acquisition_config(self, mode):
        return {
            'mode': mode,
            'exposure_time': None,
            'extra_params': {},
        }

    def _science_instrument_config(self):
        instrument_type = self._selected_instrument_type()
        extra_params = {'offset_ra': 0, 'offset_dec': 0}
        if instrument_type == 'SOAR_TRIPLESPEC':
            extra_params['rotator_angle'] = self._selected_rotator_angle()

        return {
            'exposure_time': self._selected_exposure_time(),
            'exposure_count': self._selected_exposure_count(),
            'optical_elements': {},
            'mode': self._selected_readout(),
            'rotator_mode': 'SKY',
            'extra_params': extra_params,
        }

    def _arc_config(self, science_config, priority):
        arc_config = copy.deepcopy(science_config)
        arc_config['type'] = 'ARC'
        arc_config['priority'] = priority
        arc_config['instrument_configs'][0]['exposure_time'] = 0.5
        arc_config['instrument_configs'][0]['exposure_count'] = 1
        arc_config['instrument_configs'][0]['extra_params']['rotator_angle'] = self._selected_rotator_angle()
        arc_config['acquisition_config'] = self._acquisition_config('OFF')
        return arc_config

    def _standard_config(self, science_config):
        standard_config = copy.deepcopy(science_config)
        standard_config['type'] = 'STANDARD'
        standard_config['priority'] = 2
        standard_config['instrument_configs'][0]['exposure_time'] = 65.0
        standard_config['instrument_configs'][0]['exposure_count'] = 1
        standard_config['instrument_configs'][0]['mode'] = 'fowler8_coadds1'
        standard_config['instrument_configs'][0]['extra_params']['rotator_angle'] = self._selected_rotator_angle()
        standard_config['acquisition_config'] = self._acquisition_config('OFF')
        return standard_config

    def observation_payload(self):
        payload = super().observation_payload()
        request_group = payload['requests'][0]
        request_group['configuration_repeats'] = 1
        request_group['optimization_type'] = 'TIME'

        for config in request_group.get('configurations', []):
            target = config.get('target', {})
            if target.get('epoch') is None:
                target['epoch'] = 2000.0
            if target.get('proper_motion_ra') is None:
                target['proper_motion_ra'] = 0.0
            if target.get('proper_motion_dec') is None:
                target['proper_motion_dec'] = 0.0
            if target.get('parallax') is None:
                target['parallax'] = 0.0

        science_config = request_group['configurations'][0]
        instrument_type = self._selected_instrument_type()
        science_config['type'] = 'SPECTRUM'
        science_config['instrument_type'] = instrument_type
        science_config['constraints'] = self._constraints()
        science_config['instrument_configs'] = [self._science_instrument_config()]
        science_config['acquisition_config'] = self._acquisition_config('MANUAL')
        science_config['guiding_config'] = self._guiding_config()
        science_config['priority'] = 1 if instrument_type == 'SOAR_TRIPLESPEC' else 2

        if instrument_type == 'SOAR_TRIPLESPEC':
            request_group['configurations'] = [
                science_config,
                self._standard_config(science_config),
            ]
        else:
            request_group['configurations'] = [
                self._arc_config(science_config, 1),
                science_config,
                self._arc_config(science_config, 3),
            ]

        return payload

    def _configure_proposal_field(self):
        proposal_choices = [
            choice
            for choice in self.proposal_choices()
            if "SOAR" in str(choice[0]) or "SOAR" in str(choice[1])
        ]

        if not proposal_choices:
            proposal_choices = self.proposal_choices()

        self.fields["proposal"] = forms.ChoiceField(
            choices=proposal_choices,
            initial=proposal_choices[0][0] if proposal_choices else None,
        )
    
    def _configure_instrument_fields(self):
        defaults = self.INSTRUMENT_DEFAULTS["SOAR_GHTS_REDCAM"]

        self.fields["instrument_type"] = forms.ChoiceField(
            choices=self.instrument_choices(),
            initial="SOAR_GHTS_REDCAM",
            required=False,
            label="",
        )

        self.fields["readout"].choices = self.readout_choices()

        self.fields["exposure_time"].initial = defaults["exposure_time"]
        self.fields["exposure_count"].initial = defaults["exposure_count"]
        self.fields["max_airmass"].initial = defaults["max_airmass"]
        self.fields["min_lunar_distance"].initial = 20
        self.fields["readout"].initial = defaults["readout"]
        self.fields["observation_mode"].initial = "NORMAL"

        self.fields["instrument_type"].widget.attrs.pop("required", None)
        self.fields["instrument_type"].widget.is_required = False

    def _configure_hidden_fields(self):
        for field_name in self.HIDDEN_FIELDS:
            field = self.fields.get(field_name)
            if field:
                field.widget = forms.HiddenInput()
                field.required = False

        if "groups" in self.fields:
            self.fields["groups"].widget = forms.HiddenInput()

    def _configure_required_fields(self):
        for field_name in (
            "name",
            "exposure_time",
            "exposure_count",
            "max_airmass",
            "min_lunar_distance",
            "readout",
        ):
            self.fields[field_name].required = False

    def _configure_start_end_fields(self):
        now = datetime.now()
        self.fields['start'].initial = now
        self.fields['end'].initial = now + timedelta(hours=24)

    def _configure_layout(self):
        self.helper.render_unmentioned_fields = False

        hidden_layout = [
            field
            for field in self.HIDDEN_FIELDS
            if field in self.fields
        ]

        if "groups" in self.fields:
            hidden_layout.append("groups")

        self.helper.layout = Layout(
            *hidden_layout,
            Div(
                HTML('<p>One-time SOAR submission using the default portal setup for each instrument.</p>'),
                Row(
                    Column('instrument_type', css_class='col-md-6'),
                    Column('readout', css_class='col-md-6'),
                ),
                Row(
                    Column(
                        'start',
                        css_class='col'
                    ),
                    Column(
                        'end',
                        css_class='col'
                    ),
                    css_class='form-row'
                ),
                Row(
                    Column(PrependedText('exposure_time', 'Exposure Time'), css_class='col-md-6'),
                    Column(PrependedText('exposure_count', 'Exposure Count'), css_class='col-md-6'),
                ),
                Row(
                    Column(PrependedText('max_airmass', 'Airmass <'), css_class='col-md-6'),
                    Column(PrependedText('min_lunar_distance', 'Lunar Distance >'), css_class='col-md-6'),
                ),
                Row(
                    Column(PrependedText('ipp_value', 'IPP'), css_class='col-md-6'),
                    Column('proposal', css_class='col-md-6'),
                ),
                css_class='col-12'
            ),
            self.button_layout()
        )

    def __init__(self, *args, **kwargs):
        kwargs = self._prepare_form_kwargs(kwargs)
        super().__init__(*args, **kwargs)

        if self.is_bound and hasattr(self.data, "copy"):
            data = self.data.copy()
            self._synchronize_primary_and_alias_data(data)
            self.data = data

        self._configure_proposal_field()
        self._configure_instrument_fields()
        self._configure_hidden_fields()
        self._configure_required_fields()
        self._configure_start_end_fields()
        self._configure_layout()
    

class SOARFacility(BaseSOARFacility):
    observation_types = [('SPECTRA', 'Spectra')]
    observation_forms = {'SPECTRA': SOARObservationForm}

    def get_form(self, observation_type):
        return SOARObservationForm

    def validate_observation(self, observation_payload):
        log_payload('validate', observation_payload)
        response = make_request(
            'POST',
            PORTAL_URL + '/api/requestgroups/validate/',
            json=observation_payload,
            headers=self._portal_headers()
        )
        return response.json()['errors']

    def submit_observation(self, observation_payload):
        log_payload('submit', observation_payload)
        response = make_request(
            'POST',
            PORTAL_URL + '/api/requestgroups/',
            json=observation_payload,
            headers=self._portal_headers()
        )
        return [r['id'] for r in response.json()['requests']]