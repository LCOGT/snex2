from django.conf import settings
from django import forms
from crispy_forms.layout import Layout, Div, HTML, Column, Row
from crispy_forms.bootstrap import PrependedText, AppendedText
from astropy import units as u
import datetime

from tom_observations.facilities.ocs import make_request
from tom_observations.facilities.lco import LCOPhotometricSequenceForm, LCOSpectroscopicSequenceForm, LCOFacility
from tom_observations.widgets import FilterField
from django.contrib.auth.models import Group
import logging
logger = logging.getLogger(__name__)

# Determine settings for this module.
try:
    LCO_SETTINGS = settings.FACILITIES['LCO']
except (AttributeError, KeyError):
    LCO_SETTINGS = {
        'portal_url': 'https://observe.lco.global',
        'api_key': '',
    }

# Module specific settings.
PORTAL_URL = LCO_SETTINGS['portal_url']
TERMINAL_OBSERVING_STATES = ['COMPLETED', 'CANCELED', 'WINDOW_EXPIRED']

# Units of flux and wavelength for converting to Specutils Spectrum1D objects
FLUX_CONSTANT = (1e-15 * u.erg) / (u.cm ** 2 * u.second * u.angstrom)
WAVELENGTH_UNITS = u.angstrom


class InitialValue:
    exposure_count = 2
    block_num = 1

    def __init__(self, filt):
        self.exposure_time = self.get_values_from_filt(filt)

    def get_values_from_filt(self, filt):
        initial_exp_times = {'U': 300, 'B': 200, 'V': 120, 'gp': 200, 'rp': 120, 'ip': 120}
        return initial_exp_times.get(filt, 0)

class SnexPhotometricSequenceForm(LCOPhotometricSequenceForm):    
    # Rewrite a lot of the form fields to have unique IDs between photometry and spectroscopy
    valid_instruments = ['1M0-SCICAM-SINISTRO', '0M4-SCICAM-QHY600']
    filters = ['U', 'B', 'V', 'R', 'I', 'up', 'gp', 'rp', 'ip', 'zs', 'w']

    cadence_frequency_days = forms.FloatField(required=True, min_value=0.0, initial=3.0, label='')
    cadence_frequency = forms.FloatField(widget=forms.HiddenInput(), required=False)
    reminder = forms.FloatField(required=True, min_value=0.0, initial=6.7, label='Reminder in')
    comment = forms.CharField(required=False, label='Comments', widget=forms.Textarea(attrs={'cols': 30, 'rows': 3}))
    delay_start = forms.BooleanField(required=False, label='Delay Start By')
    delay_amount = forms.FloatField(initial=0.0, min_value=0, label='', required=False)
    
    def __init__(self, *args, **kwargs):
        super(LCOPhotometricSequenceForm, self).__init__(*args, **kwargs)
        
        self.fields['cadence_strategy'] = forms.ChoiceField(
            choices=[('ResumeCadenceAfterFailureStrategy', 'Repeating every'), ('SnexRetryFailedObservationsStrategy', 'Once in the next')],
            required=False,
            label=''
        )

        self.fields['ipp_value'].initial = 1.0
        self.fields['max_airmass'].initial = 1.6
        self.fields['min_lunar_distance'].initial = 20

        # Add fields for each available filter as specified in the filters property
        for filter_name in self.filters:
            self.fields[filter_name] = FilterField(label='', initial=InitialValue(filter_name), required=False)

        # Set default proposal to GSP
        proposal_choices = self.proposal_choices()
        initial_proposal = ''
        for choice in proposal_choices:
            if 'Global Supernova Project' in choice[1]:
                initial_proposal = choice
        self.fields['proposal'] = forms.ChoiceField(choices=proposal_choices, initial=initial_proposal)
        self.fields['start'] = forms.CharField(widget=forms.TextInput(attrs={'type': 'date'}))
        self.fields['end'] = forms.CharField(widget=forms.TextInput(attrs={'type': 'date'}))
    
        # Massage cadence form to be SNEx-styled
        self.fields['name'].label = ''
        self.fields['name'].widget.attrs['placeholder'] = 'Name'
        
        for field_name in ['exposure_time', 'exposure_count', 'filter']:
            self.fields.pop(field_name)
        
        for field_name in ['start', 'end']:
            self.fields[field_name].widget = forms.HiddenInput()
            self.fields[field_name].required = False
        
        if not settings.TARGET_PERMISSIONS_ONLY:
            self.fields['groups'] = forms.ModelMultipleChoiceField(
                    Group.objects.all(),
                    initial = Group.objects.filter(name__in=settings.DEFAULT_GROUPS),
                    required=False,
                    widget=forms.CheckboxSelectMultiple, 
                    label='Data granted to')
        
        self.fields['instrument_type'] = forms.ChoiceField(choices=self.instrument_choices(), initial=('1M0-SCICAM-SINISTRO', '1.0 meter Sinistro'))
       
        self.helper.layout = Layout(
            Div(
                Column('name'),
                Column('cadence_strategy'),
                Column(AppendedText('cadence_frequency_days', 'Days')),
                css_class='form-row'
            ),
            Div(
                Column(),
                Column('delay_start'),
                Column(AppendedText('delay_amount', 'Days')),
                css_class='form-row'
            ),
            Layout('facility', 'target_id', 'observation_type'),
            self.layout(),
            self.button_layout()
        )

    def clean(self):
        """
        This clean method does the following:
            - Adds a start time of "right now", as the photometric sequence form does not allow for specification
              of a start time.
            - Adds an end time that corresponds with the cadence frequency
            - Adds the cadence strategy to the form if "repeat" was the selected "cadence_type". If "once" was
              selected, the observation is submitted as a single observation.
        """
        self.cleaned_data['cadence_frequency'] = self.cleaned_data.get('cadence_frequency_days') * 24
        cleaned_data = super().clean()
        now = datetime.datetime.utcnow()
        if cleaned_data.get('delay_start'):
            cleaned_data['start'] = datetime.datetime.strftime(now + datetime.timedelta(days=cleaned_data['delay_amount']), '%Y-%m-%dT%H:%M:%S')
            cleaned_data['end'] = datetime.datetime.strftime(now + datetime.timedelta(hours=cleaned_data['cadence_frequency']+cleaned_data['delay_amount']*24), '%Y-%m-%dT%H:%M:%S')
        reminder = cleaned_data.get('reminder', 6.7)
        cleaned_data['reminder'] = reminder
        reminder_date = now + datetime.timedelta(days=reminder)
        cleaned_data['reminder_date'] = reminder_date.strftime('%Y-%m-%dT%H:%M:%S')
        cleaned_data = {k: ([] if isinstance(v, list) and len(v) == 3 and (v[0] == 0 or v[1] == 0 or v[2] == 0) else v) for k, v in cleaned_data.items()}
        logger.info(f'snex2 cleaned data with 0 exp time filters replaced with empty lists: {cleaned_data}')
        return cleaned_data

    def layout(self):
        if settings.TARGET_PERMISSIONS_ONLY:
            groups = Div()
        else:
            groups = Row('groups')

        filter_container = Div(css_class='form-row', css_id='all-filters-div')

        for filter_name in self.filters:
            filter_container.append(
                Row(
                    PrependedText(filter_name, filter_name), 
                    css_id=f'row_{filter_name}'
                )
            )

        return Div(
            Div(
                Row(Column(HTML('Exposure Time')), Column(HTML('No. of Exposures')), Column(HTML('Block No.'))),
                filter_container, 
                Row('comment'),
                css_class='col-md-6'
            ),
            Div(
                Div(
                    Row('max_airmass'),
                    Row(
                        PrependedText('min_lunar_distance', '>')
                    ),
                    Row('instrument_type'),
                    Row('proposal'),
                    Row('observation_mode'),
                    Row('ipp_value'),
                    Row(AppendedText('reminder', 'days')),
                    css_class='form-row',
                ),
                Div(
                    groups,
                    css_class='form-row'
                ),
                css_class='col-md-6'
            ),
            css_class='form-row'
        )

    def instrument_choices(self):
        """
        This method returns only the instrument choices available in the current SNEx photometric sequence form.
        """
        return sorted([(k, v['name'])
                    for k, v in self._get_instruments().items()
                    if k in self.valid_instruments],
                    key=lambda inst: inst[1])

    def _build_instrument_config(self):
        instrument_config = []
        for filter_name in self.filters:
            if len(self.cleaned_data[filter_name]) > 0:
                if filter_name in ['U', 'R', 'I'] and self.cleaned_data['instrument_type'] == '0M4-SCICAM-QHY600':
                    continue
                if self.cleaned_data[filter_name][0] > 0 and self.cleaned_data[filter_name][1] > 0:
                    instrument_config.append({
                        'exposure_count': self.cleaned_data[filter_name][1],
                        'exposure_time': self.cleaned_data[filter_name][0],
                        'optical_elements': {
                            'filter': filter_name
                        }
                    })

        return instrument_config


class SnexSpectroscopicSequenceForm(LCOSpectroscopicSequenceForm):
    exposure_count = forms.IntegerField(min_value=1, required=False, initial=1, widget=forms.HiddenInput())
    cadence_frequency = forms.FloatField(required=True, min_value=0.0, initial=3.0, widget=forms.NumberInput(attrs={'placeholder': 'Days'}), label='')
    max_airmass = forms.FloatField(initial=1.6, min_value=0, label='Max Airmass')
    acquisition_radius = forms.FloatField(min_value=0, required=False, initial=5.0)
    guider_exposure_time = forms.FloatField(min_value=0, initial=10.0)
    name = forms.CharField()
    ipp_value = forms.FloatField(label='Intra Proposal Priority (IPP factor)',
                                 min_value=0.5,
                                 max_value=2,
                                 initial=1.0)
    min_lunar_distance = forms.IntegerField(min_value=0, label='Minimum Lunar Distance', initial=20, required=False)
    exposure_time = forms.IntegerField(min_value=1,
                                     widget=forms.TextInput(attrs={'placeholder': 'Seconds'}),
                                     initial=1800)
    reminder = forms.FloatField(required=True, min_value=0.0, initial=6.7, label='Reminder in')
    comment = forms.CharField(required=False, label='Comments', widget=forms.Textarea(attrs={'cols': 30, 'rows': 3}))
    delay_start = forms.BooleanField(required=False, label='Delay Start By')
    delay_amount = forms.FloatField(initial=0.0, min_value=0, label='', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Massage cadence form to be SNEx-styled
        self.fields['filter'] = forms.ChoiceField(choices=self.all_optical_element_choices(),
                                                  label='Slit',
                                                  initial=('slit_2.0as', '2.0 arcsec slit'))
        self.fields['name'].label = ''
        self.fields['name'].widget.attrs['placeholder'] = 'Name'
        self.fields['min_lunar_distance'].widget.attrs['placeholder'] = 'Degrees'
        self.fields['ipp_value'].label = 'IPP'
        self.fields['cadence_strategy'] = forms.ChoiceField(
            choices=[('ResumeCadenceAfterFailureStrategy', 'Repeating every'), ('SnexRetryFailedObservationsStrategy', 'Once in the next')],
            required=False,
            label=''
        )
        self.fields['instrument_type'] = forms.ChoiceField(choices=self.instrument_choices(),
                                                           required=False,
                                                           initial='2M0-FLOYDS-SCICAM',
                                                           widget=forms.HiddenInput())

        self.fields['start'] = forms.CharField(widget=forms.TextInput(attrs={'type': 'date'}))
        self.fields['end'] = forms.CharField(widget=forms.TextInput(attrs={'type': 'date'}))
        # Set default proposal to GSP
        proposal_choices = self.proposal_choices()
        initial_proposal = ''
        for choice in proposal_choices:
            if 'Global Supernova Project' in choice[1]:
                initial_proposal = choice
        self.fields['proposal'] = forms.ChoiceField(choices=proposal_choices, initial=initial_proposal)

        for field_name in ['start', 'end']:
            self.fields[field_name].widget = forms.HiddenInput()
            self.fields[field_name].required = False

        if self.fields.get('groups'):
            self.fields['groups'].label = 'Data granted to'
            self.fields['groups'].initial = Group.objects.filter(name__in=settings.DEFAULT_GROUPS)
        
        self.helper.layout = Layout(
            Div(
                Column('name'),
                Column('cadence_strategy'),
                Column(AppendedText('cadence_frequency', 'Days')),
                css_class='form-row'
            ),
            Div(
                Column(),
                Column('delay_start'),
                Column(AppendedText('delay_amount', 'Days')),
                css_class='form-row'
            ),
            Layout('facility', 'target_id', 'observation_type'),
            self.layout(),
            self.button_layout()
        )

    def clean(self):
        cleaned_data = super().clean()
        self.cleaned_data['instrument_type'] = '2M0-FLOYDS-SCICAM'  # SNEx only submits spectra to FLOYDS
        now = datetime.datetime.utcnow()
        cleaned_data['cadence_frequency'] *= 24 
        if cleaned_data.get('delay_start'):
            cleaned_data['start'] = datetime.datetime.strftime(now + datetime.timedelta(days=cleaned_data['delay_amount']), '%Y-%m-%dT%H:%M:%S')
            cleaned_data['end'] = datetime.datetime.strftime(now + datetime.timedelta(hours=cleaned_data['cadence_frequency']*24+cleaned_data['delay_amount']*24), '%Y-%m-%dT%H:%M:%S')
        else:
            cleaned_data['start'] = datetime.datetime.strftime(now, '%Y-%m-%dT%H:%M:%S')
            cleaned_data['end'] = datetime.datetime.strftime(now + datetime.timedelta(hours=cleaned_data['cadence_frequency']*24), '%Y-%m-%dT%H:%M:%S')
        cleaned_data['reminder'] = datetime.datetime.strftime(now + datetime.timedelta(days=cleaned_data['reminder']), '%Y-%m-%dT%H:%M:%S')
        return cleaned_data
    
    def layout(self):
        if settings.TARGET_PERMISSIONS_ONLY:
            groups = Div()
        else:
            groups = Row('groups')
        return Div(
            Div(
                Row('exposure_count'),
                Row('exposure_time'),
                Row('max_airmass'),
                Row(PrependedText('min_lunar_distance', '>')),
                Row('site'),
                Row('filter'),
                groups,
                css_class='col-md-6'
            ),
            Div(
                Row('acquisition_radius'),
                Row('guider_mode'),
                Row('guider_exposure_time'),
                Row('proposal'),
                Row('observation_mode'),
                Row('ipp_value'),
                Row(AppendedText('reminder', 'days')),
                Row('comment'),
                css_class='col-md-6'
            ),
        css_class='form-row')

class SnexLCOFacility(LCOFacility):
    name = 'LCO'
    observation_types = [('IMAGING', 'Imaging'),
                         ('SPECTRA', 'Spectra')]
    observation_forms = {
        'IMAGING': SnexPhotometricSequenceForm,
        'SPECTRA': SnexSpectroscopicSequenceForm
    }

    def submit_observation(self, observation_payload):
        for request in observation_payload.get('requests', []):
            for config in request.get('configurations', []):
                if not config.get('type'):
                    config['type'] = 'EXPOSE'
        response = make_request(
            'POST',
            PORTAL_URL + '/api/requestgroups/',
            json=observation_payload,
            headers=self._portal_headers()
        )
        return [r['id'] for r in response.json()['requests']]

    def validate_observation(self, observation_payload):
        response = make_request(
            'POST',
            PORTAL_URL + '/api/requestgroups/validate/',
            json=observation_payload,
            headers=self._portal_headers()
        )
        return response.json()['errors']

