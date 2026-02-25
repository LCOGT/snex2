from django.conf import settings
from django import forms
from crispy_forms.layout import Layout, Div, HTML, Column, Row
from crispy_forms.bootstrap import PrependedText, AppendedText
from astropy import units as u
import datetime
import copy

from tom_observations.facilities.ocs import make_request
from tom_observations.facilities.lco import LCOPhotometricSequenceForm, LCOSpectroscopicSequenceForm, LCOFacility, LCOSettings
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
    valid_instruments = ['1M0-SCICAM-SINISTRO', '0M4-SCICAM-QHY600', '2M0-SCICAM-MUSCAT']
    filters = ['U', 'B', 'V', 'R', 'I', 'up', 'gp', 'rp', 'ip', 'zs', 'w', 'muscat_filter']

    cadence_frequency_value = forms.FloatField(required=True, min_value=0.0, initial=3.0, label='')
    cadence_frequency_unit = forms.ChoiceField(choices=[('days', 'Days'), ('hours', 'Hours')], initial='days', label='')
    cadence_frequency = forms.FloatField(widget=forms.HiddenInput(), required=False)
    reminder = forms.FloatField(required=True, min_value=0.0, initial=6.7, label='Reminder in')
    comment = forms.CharField(required=False, label='Comments', widget=forms.Textarea(attrs={'cols': 30, 'rows': 3}))
    delay_start = forms.BooleanField(required=False, label='Delay Start By')
    delay_amount = forms.FloatField(initial=0.0, min_value=0, label='', required=False)
    
    def __init__(self, *args, **kwargs):
        super(LCOPhotometricSequenceForm, self).__init__(*args, **kwargs)
        
        self.fields['cadence_strategy'] = forms.ChoiceField(
            choices=[('SnexResumeCadenceAfterFailureStrategy', 'Repeating every'), ('SnexRetryFailedObservationsStrategy', 'Once in the next')],
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
                Column('name', css_class='col-md-4'),
                Column('cadence_strategy', css_class='col-md-4'),
                Column(
                    Row(
                        Column('cadence_frequency_value', css_class='col-8 pr-1'),
                        Column('cadence_frequency_unit', css_class='col-4 pl-1'),
                    ),
                    css_class='col-md-4'
                ),
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
        
        if self.cleaned_data['cadence_frequency_unit'].lower() == 'hours':
            self.cleaned_data['cadence_frequency'] = self.cleaned_data['cadence_frequency_value']
            self.cleaned_data['cadence_frequency_days'] = self.cleaned_data['cadence_frequency_value'] / 24

        else:
            self.cleaned_data['cadence_frequency'] = self.cleaned_data['cadence_frequency_value'] * 24
            self.cleaned_data['cadence_frequency_days'] = self.cleaned_data['cadence_frequency_value']

        cleaned_data = super().clean()
        existing_reminder = self.data.get('reminder_date')
        now = datetime.datetime.utcnow()
        if cleaned_data.get('delay_start'):
            cleaned_data['start'] = datetime.datetime.strftime(now + datetime.timedelta(days=cleaned_data['delay_amount']), '%Y-%m-%dT%H:%M:%S')
            cleaned_data['end'] = datetime.datetime.strftime(now + datetime.timedelta(hours=cleaned_data['cadence_frequency']+cleaned_data['delay_amount']*24), '%Y-%m-%dT%H:%M:%S')
        if existing_reminder:
            cleaned_data['reminder_date'] = existing_reminder
        else:
            reminder = cleaned_data.get('reminder')
            delay = cleaned_data.get('delay_amount', 0.0)
            
            calculated_date = now + datetime.timedelta(days=reminder + delay)
            cleaned_data['reminder_date'] = calculated_date.strftime('%Y-%m-%dT%H:%M:%S')
            
        cleaned_data = {k: ([] if isinstance(v, list) and len(v) == 3 and (v[0] == 0 or v[1] == 0 or v[2] == 0) else v) for k, v in cleaned_data.items()}
        return cleaned_data

    def layout(self):
        if settings.TARGET_PERMISSIONS_ONLY:
            groups = Div()
        else:
            groups = Row('groups')

        filter_container = Div(css_class='form-row', css_id='all-filters-div')

        for filter_name in self.filters:
            label_name = filter_name
            if filter_name == 'muscat_filter':
                label_name = 'gp, rp, ip, zs'
            filter_container.append(
                Row(
                    PrependedText(filter_name, label_name), 
                    css_id=f'row_{filter_name}'
                )
            )

        return Div(
            Div(
                Row(Column(HTML('Exposure Time'), css_id = 'exp-time-header'), Column(HTML('No. of Exposures'), css_id = 'exp-num-header'), Column(HTML('Block No.'), css_id = 'block-no-header')),
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
    
    def observation_payload(self):
        payload = super().observation_payload()
        instrument_type = self.cleaned_data.get('instrument_type')
        
        if instrument_type == '2M0-SCICAM-MUSCAT':
            muscat_configs = self._build_instrument_config()
            
            if 'requests' in payload:
                for request in payload['requests']:
                    for configuration in request.get('configurations', []):
                        configuration['instrument_configs'] = muscat_configs
                        configuration['instrument_type'] = instrument_type
                        if not configuration.get('type'):
                            configuration['type'] = 'EXPOSE'
            
            elif 'configurations' in payload:
                for configuration in payload['configurations']:
                    configuration['instrument_configs'] = muscat_configs
                    configuration['instrument_type'] = instrument_type
                    if not configuration.get('type'):
                        configuration['type'] = 'EXPOSE'

        return payload

    def _build_instrument_config(self):
        instrument_config = []
        instrument_type = self.cleaned_data.get('instrument_type')

        if instrument_type == '2M0-SCICAM-MUSCAT':
            muscat_data = self.cleaned_data.get('muscat_filter')
            if muscat_data and len(muscat_data) > 1 and muscat_data[0] > 0:
                exp_time = muscat_data[0]
                exp_count = muscat_data[1]
                
                instrument_config.append({
                    'exposure_time': exp_time,
                    'exposure_count': exp_count,
                    'mode': 'MUSCAT_SLOW',
                    'optical_elements': {
                        'narrowband_g_position': 'out',
                        'narrowband_i_position': 'out',
                        'narrowband_r_position': 'out',
                        'narrowband_z_position': 'out'
                    },
                    'extra_params': {
                        'bin_x': 1,
                        'bin_y': 1,
                        'exposure_mode': 'SYNCHRONOUS',
                        'exposure_time_g': exp_time,
                        'exposure_time_i': exp_time,
                        'exposure_time_r': exp_time,
                        'exposure_time_z': exp_time
                    }
                })
            return instrument_config

        for filter_name in self.filters:
            if filter_name == 'muscat_filter':
                continue
                
            if len(self.cleaned_data[filter_name]) > 0:
                if filter_name in ['U', 'R', 'I'] and instrument_type == '0M4-SCICAM-QHY600':
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
    cadence_frequency_value = forms.FloatField(required=True, min_value=0.0, initial=3.0, label='')
    cadence_frequency_unit = forms.ChoiceField(choices=[('days', 'Days'), ('hours', 'Hours')], initial='days', label='')
    cadence_frequency = forms.FloatField(widget=forms.HiddenInput(), required=False)
    site = forms.CharField(widget=forms.HiddenInput(), initial='any', required=False)
    exposure_time = forms.IntegerField(min_value=1,
                                     widget=forms.TextInput(attrs={'placeholder': 'Seconds'}),
                                     initial=1800)
    reminder = forms.FloatField(required=True, min_value=0.0, initial=6.7, label='Reminder in')
    comment = forms.CharField(required=False, label='Comments', widget=forms.Textarea(attrs={'cols': 30, 'rows': 3}))
    delay_start = forms.BooleanField(required=False, label='Delay Start By')
    delay_amount = forms.FloatField(initial=0.0, min_value=0, label='', required=False)
                
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # set form defaults
        self.fields['acquisition_radius'].initial = 0
        self.fields['max_airmass'].initial = 1.6
        self.fields['guider_exposure_time'].initial = 10.0
        self.fields['ipp_value'].initial = 1.0
        self.fields['min_lunar_distance'].initial = 20

        self.fields['filter'] = forms.ChoiceField(choices=self.all_optical_element_choices(),
                                                  label='Slit',
                                                  initial=('slit_2.0as', '2.0 arcsec slit'))
        self.fields['cadence_strategy'] = forms.ChoiceField(
            choices=[('SnexResumeCadenceAfterFailureStrategy', 'Repeating every'), ('SnexRetryFailedObservationsStrategy', 'Once in the next')],
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
                Column('name', css_class='col-md-4'),
                Column('cadence_strategy', css_class='col-md-4'),
                Column(
                    Row(
                        Column('cadence_frequency_value', css_class='col-8 pr-1'),
                        Column('cadence_frequency_unit', css_class='col-4 pl-1'),
                    ),
                    css_class='col-md-4'
                ),
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
        if self.cleaned_data['cadence_frequency_unit'].lower() == 'hours':
            self.cleaned_data['cadence_frequency'] = self.cleaned_data['cadence_frequency_value']
            self.cleaned_data['cadence_frequency_days'] = self.cleaned_data['cadence_frequency_value'] / 24

        else:
            self.cleaned_data['cadence_frequency'] = self.cleaned_data['cadence_frequency_value'] * 24
            self.cleaned_data['cadence_frequency_days'] = self.cleaned_data['cadence_frequency_value']

        cleaned_data = super().clean()
        self.cleaned_data['instrument_type'] = '2M0-FLOYDS-SCICAM'  # SNEx only submits spectra to FLOYDS
        existing_reminder = self.data.get('reminder_date')
        now = datetime.datetime.utcnow()
        if cleaned_data.get('delay_start'):
            cleaned_data['start'] = datetime.datetime.strftime(now + datetime.timedelta(days=cleaned_data['delay_amount']), '%Y-%m-%dT%H:%M:%S')
            cleaned_data['end'] = datetime.datetime.strftime(now + datetime.timedelta(hours=cleaned_data['cadence_frequency']+cleaned_data['delay_amount']*24), '%Y-%m-%dT%H:%M:%S')
        if existing_reminder:
            cleaned_data['reminder_date'] = existing_reminder
        else:
            reminder = cleaned_data.get('reminder')
            delay = cleaned_data.get('delay_amount', 0.0)
            
            calculated_date = now + datetime.timedelta(days=reminder + delay)
            cleaned_data['reminder_date'] = calculated_date.strftime('%Y-%m-%dT%H:%M:%S')
            
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
                Row('guider_mode'),
                Row('guider_exposure_time'),
                Row('comment'),
                css_class='col-md-6'
            ),
            Div(
                Row('acquisition_radius'),
                Row('proposal'),
                Row('observation_mode'),
                Row('ipp_value'),
                Row(AppendedText('reminder', 'days')),
                groups,
                css_class='col-md-6'
            ),
        css_class='form-row')
    
    def observation_payload(self):

        payload = super().observation_payload()
        
        request_group = payload['requests'][0]
        science_config = request_group['configurations'][0]
        
        science_config['type'] = 'SPECTRUM'
        science_config['instrument_type'] = '2M0-FLOYDS-SCICAM'
        
        slit_val = self.cleaned_data.get('filter', 'slit_2.0as')
        science_config['instrument_configs'][0]['optical_elements'] = {'slit': slit_val}
        
        flat_config = copy.deepcopy(science_config)
        flat_config['type'] = 'LAMP_FLAT'
        flat_config['instrument_configs'][0]['exposure_time'] = 40.0
        flat_config['acquisition_config'] = {"mode": "OFF"}
        flat_config['guiding_config'] = {"mode": "ON", "optional": True}
        
        arc_config = copy.deepcopy(science_config)
        arc_config['type'] = 'ARC'
        arc_config['instrument_configs'][0]['exposure_time'] = 80.0
        arc_config['acquisition_config'] = {"mode": "OFF"}
        arc_config['guiding_config'] = {"mode": "ON", "optional": True}
        
        configurations = [science_config, arc_config, flat_config]
        
        request_group['configurations'] = configurations
        
        return payload

class SnexLCOFacility(LCOFacility):
    name = 'LCO'
    observation_types = [('IMAGING', 'Imaging'),
                         ('SPECTRA', 'Spectra')]
    observation_forms = {
        'IMAGING': SnexPhotometricSequenceForm,
        'SPECTRA': SnexSpectroscopicSequenceForm
    }

    def __init__(self, facility_settings=LCOSettings('LCO')):
        super().__init__(facility_settings=facility_settings)
