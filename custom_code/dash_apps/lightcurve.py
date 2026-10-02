import dash_core_components as dcc
import dash_bootstrap_components as dbc
from dash import html
import plotly.graph_objs as go
from dash.dependencies import Input, Output
import json
import numpy as np
from django_plotly_dash import DjangoDash
from tom_dataproducts.models import ReducedDatum
from django.conf import settings
from django.contrib.auth.models import User
from tom_targets.models import Target
from custom_code.models import ReducedDatumExtra
from custom_code.utils import measured
import logging
from django.templatetags.static import static
from datetime import datetime, timezone
from astropy.time import Time
from guardian.shortcuts import get_objects_for_user

logger = logging.getLogger(__name__)

app = DjangoDash(name='Lightcurve', add_bootstrap_links=True)
app.css.append_css({'external_url': static('custom_code/css/dash.css')})
telescopes = ['LCO']
reducer_groups = []
app.layout = html.Div([
    dcc.Graph(
        id='lightcurve-plot',
        style={'width': '100%'},
        config={'responsive': True}
    ),
    dcc.Input(
        id='target_id',
        type='hidden',
        value=0
    ),
    dcc.Input(
        id='user_id',
        type='hidden',
        value=0
    ),
    dcc.Input(
        id='plot-height',
        type='hidden',
        value=300
    ),
    html.Button(
        'Toggle plotting options',
        id='show-btn',
        n_clicks=0,
        style={'background-color': 'white',
               'cursor': 'pointer',
               'padding': '10px',
               'margin': '10px',
               'border-color': '#174460',
               'color': '#174460'}
    ),
    html.Div(
        id='plotting-options',
        style={'display': 'none', 'padding': '0 15px'},
        children=[
            html.H4('Instrument'),
            dcc.Checklist(
                id='telescopes-checklist',
                options=[{'label': k, 'value': k} for k in telescopes],
                value=telescopes,
                inputStyle={"margin-right": "5px", "margin-left": "5px"}
            ),
            html.Hr(),
            dbc.Row(
                [
                    dbc.Col(
                        [
                            dbc.Row(html.H4('Difference Imaging')),
                            dbc.Row(dcc.RadioItems(
                                id='subtracted-radio',
                                options=[{'label': 'Unsubtracted', 'value': 'Unsubtracted'},
                                         {'label': 'Subtracted', 'value': 'Subtracted'}
                                ],
                                value='Unsubtracted',
                                inputStyle={"margin-right": "5px", "margin-left": "5px"},
                                style={'display': 'none'},
                            ))
                        ], 
                    width=4),
                    dbc.Col(
                        [
                            dbc.Row(html.H4('Magnitude Type')),
                            dbc.Row(dcc.RadioItems(
                                id='magnitude-type-radio',
                                options=[{'label': 'Apparent', 'value': 'Apparent'},
                                         {'label': 'PSF', 'value': 'PSF'},
                                         {'label': 'Aperture', 'value': 'Aperture'}
                                ],
                                value='Apparent',
                                inputStyle={"margin-right": "5px", "margin-left": "5px"},
                            )),
                            dbc.Row(html.H4('Reduction')),
                            dbc.Row(dcc.Checklist(
                                id='final-only-checklist',
                                options=[{'label': 'Final only', 'value': 'final'}],
                                value=[],
                                inputStyle={"margin-right": "5px", "margin-left": "5px"},
                            ))
                        ],
                    width=4),
                    dbc.Col(html.Div(
                        id='subtracted-extras',
                        children=[
                            html.H5('Subtraction Algorithm'),
                            dcc.Checklist(
                                id='algorithm-checklist',
                                options=[{'label': 'Hotpants', 'value': 'Hotpants'},
                                         {'label': 'PyZOGY', 'value': 'PyZOGY'}
                                ],
                                value=['Hotpants', 'PyZOGY'],
                                inputStyle={"margin-right": "5px", "margin-left": "5px"}
                            ),
                            html.H5('Template Source'),
                            dcc.Checklist(
                                id='template-checklist',
                                options=[{'label': 'LCO', 'value': 'LCO'},
                                         {'label': 'SDSS', 'value': 'SDSS'},
                                         {'label': 'PS1', 'value': 'PS1'}
                                ],
                                value=['LCO', 'SDSS', 'PS1'],
                                inputStyle={"margin-right": "5px", "margin-left": "5px"}
                            )
                        ],
                        style={'display': 'none'}
                    ), width=4)
                    ], style={'margin-left': '1px'}
            ),
            html.Hr(),
            html.H4('Data from Group'),
            dcc.Checklist(
                id='reducer-group-checklist',
                options=[{'label': 'LCO', 'value': ''}],
                value=[''],
                inputStyle={"margin-right": "5px", "margin-left": "5px"}
            ),
            html.Hr(),
            html.P(id='frame-info', children=''),
        ],
    ),
    html.Div(
        id='display-selected-values')
])

#Show photometry plotting options if button is pressed
@app.callback(
        Output('plotting-options', 'style'),
        [Input('show-btn', 'n_clicks')]
)
def display_options(n_clicks):
    if (n_clicks % 2) == 0:
        return {'display': 'none', 'padding': '0 15px'}
    else:
        return {'padding': '0 15px'}


#Hide subtracted choices if LCO telescope is not selected
@app.callback(
        Output('subtracted-radio', 'style'),
        [Input('telescopes-checklist', 'value')])
def update_subtracted_style(selected_telescope):
    if 'LCO' in selected_telescope:
        return {}
    else:
        return {'display': 'none'}

#Only show algorithm choices if subtracted data is selected
@app.callback(
        Output('subtracted-extras', 'style'),
        [Input('subtracted-radio', 'value')])
def update_algorith_style(selected_subtraction):
    if selected_subtraction == 'Subtracted':
        return {}
    else:
        return {'display': 'none'}

#Automatically select both subtraction algorithms when subtracted data is selected
@app.callback(
        Output('algorithm-checklist', 'value'),
        [Input('subtracted-radio', 'value')])
def update_algorithm_value(selected_subtraction):
    return ['Hotpants', 'PyZOGY']

#Automatically select both template choices when subtracted data is selected
@app.callback(
        Output('template-checklist', 'value'),
        [Input('subtracted-radio', 'value')])
def update_template_value(selected_subtraction):
    return ['LCO', 'SDSS', 'PS1']

@app.callback(
        Output('lightcurve-plot', 'figure'),
        [Input('telescopes-checklist', 'value'),
         Input('subtracted-radio', 'value'),
         Input('algorithm-checklist', 'value'),
         Input('template-checklist', 'value'),
         Input('reducer-group-checklist', 'value'),
         Input('target_id', 'value'),
         Input('user_id', 'value'),
         Input('plot-height', 'value'),
         Input('magnitude-type-radio', 'value'),
         Input('final-only-checklist', 'value')])
def update_graph(selected_telescope, subtracted_value, selected_algorithm, selected_template, selected_groups, target_id, user_id, height, magnitude_type, final_only):
    def get_color(filter_name, filter_translate):
        colors = {'U': 'rgb(59,0,113)',
            'u': 'rgb(59,0,113)',
            'B': 'rgb(0,87,255)',
            'V': 'rgb(120,255,0)',
            'g': 'rgb(0,204,255)',
            'r': 'rgb(255,124,0)',
            'i': 'rgb(144,0,43)',
            'g_ZTF': 'rgb(0,204,255)',
            'r_ZTF': 'rgb(255,124,0)',
            'i_ZTF': 'rgb(144,0,43)',
            'R': 'rgb(224,27,27)',
            'I': 'rgb(125,10,60)',
            'zs': 'rgb(101,42,42)',
            'w': 'rgb(120,120,120)',
            'UVW2': '#FE0683',
            'UVM2': '#BF01BC',
            'UVW1': '#8B06FF',
            'other': 'rgb(0,0,0)'}
        try: color = colors[filter_translate[filter_name]]
        except: color = colors['other']
        return color

    logger.info('Plotting dash lightcurve for target %s', target_id)

    filter_translate = {'U': 'U', 'B': 'B', 'V': 'V', 'R': 'R', 'I': 'I',
        'up': 'u', 'u': 'u', 'g': 'g', 'gp': 'g', 'r': 'r', 'rp': 'r', 'i': 'i', 'ip': 'i',
        'zs': 'zs', 'z': 'zs', 'w': 'w',
        'g_ZTF': 'g_ZTF', 'r_ZTF': 'r_ZTF', 'i_ZTF': 'i_ZTF', 'UVW2': 'UVW2', 'UVM2': 'UVM2',
        'UVW1': 'UVW1'}
    magnitude_key, error_key = {'PSF': ('psfmag', 'psfdmag'), 'Aperture': ('apmag', 'dapmag')}.get(magnitude_type, ('magnitude', 'error'))
    photometry_data = {}
    subtracted_photometry_data = {}
    target = Target.objects.get(id=target_id)
    user = User.objects.get(id=user_id)
    datumextras = get_objects_for_user(user, 'custom_code.view_reduceddatumextra',
                                       klass=ReducedDatumExtra.objects.filter(
                                           target=target,key='upload_extras',
                                           data_type='photometry'))
    
    datums = []
    final_products = {de.value.get('data_product_id') for de in datumextras if de.value.get('final_reduction')}
    
    ### Get the data for the selected telescope
    if not selected_telescope:
        if settings.TARGET_PERMISSIONS_ONLY:
            datums.append(ReducedDatum.objects.filter(target=target, data_type='photometry', value__has_key='filter'))
        else:
            datums.append(get_objects_for_user(user, 'tom_dataproducts.view_reduceddatum',
                                               klass=ReducedDatum.objects.filter(
                                                   target=target, data_type='photometry', 
                                                   value__has_key='filter')))
    else:
        for de in datumextras:
            de_value = de.value

            if de_value.get('instrument', '') in selected_telescope and de_value.get('reducer_group', '') in selected_groups:
                dp_id = de_value.get('data_product_id', '')
                datums.append(get_objects_for_user(user, 'tom_dataproducts.view_reduceddatum',
                                                   klass=ReducedDatum.objects.filter(
                                                       target=target, data_type='photometry', 
                                                       data_product_id=dp_id, value__has_key='filter')))
        
        ### Finally, get the data that was uploaded by the pipeline
        if 'LCO' in selected_telescope and '' in selected_groups:
            datums.append(get_objects_for_user(user, 'tom_dataproducts.view_reduceddatum',
                                               klass=ReducedDatum.objects.filter(
                                                   target=target, data_type='photometry', 
                                                   data_product_id__isnull=True, value__has_key='filter')))
    
    ### Plot the data
    spec = get_objects_for_user(user, 'tom_dataproducts.view_reduceddatum',
                                klass=ReducedDatum.objects.filter(
                                    target=target, data_type='spectroscopy'))
        
    for data in datums:
        for rd in data:
            value = rd.value
            if not value:
                continue
            if isinstance(value, str):
                value = json.loads(value)

            if measured(value.get(magnitude_key)) is None:
                continue

            if final_only and not (value.get('final_reduction') or rd.data_product_id in final_products):
                continue

            if value.get('background_subtracted', '') == True:
                if value.get('subtraction_algorithm', '') in selected_algorithm and value.get('template_source', '') in selected_template:
                    
                    raw_filter = value.get('filter', '')
                    subtracted_filt = filter_translate.get(raw_filter, raw_filter or 'other')

                    subtracted_photometry_data.setdefault(subtracted_filt, {})
                    subtracted_photometry_data[subtracted_filt].setdefault('time', []).append(rd.timestamp)
                    subtracted_photometry_data[subtracted_filt].setdefault('magnitude', []).append(value.get(magnitude_key))
                    subtracted_photometry_data[subtracted_filt].setdefault('error', []).append(measured(value.get(error_key)) or 0)
            else:

                raw_filter = value.get('filter', '')
                filt = filter_translate.get(raw_filter, raw_filter or 'other')

                photometry_data.setdefault(filt, {})
                photometry_data[filt].setdefault('time', []).append(rd.timestamp)
                photometry_data[filt].setdefault('magnitude', []).append(value.get(magnitude_key))
                photometry_data[filt].setdefault('error', []).append(measured(value.get(error_key)) or 0)

    if subtracted_value == 'Unsubtracted':
        selected_photometry = photometry_data
    elif subtracted_value == 'Subtracted':
        selected_photometry = subtracted_photometry_data

    plot_data = [
        go.Scatter(
            x=[(datetime.now(timezone.utc) - t.replace(tzinfo=timezone.utc)).total_seconds()/(24*3600) for t in filter_values['time']],
            y=filter_values['magnitude'], 
            mode='markers',
            marker=dict(color=get_color(filter_name, filter_translate),
                        line=dict(color='black', width=1)
            ),
            name=filter_translate.get(filter_name, filter_name or 'other'),
            error_y=dict(
                type='data',
                array=filter_values['error'],
                visible=True,
                color=get_color(filter_name, filter_translate)
            ),
            text=['{} (MJD {})'.format(t.strftime('%m/%d/%Y'), str(round(Time(t).mjd, 2))) for t in filter_values['time']],
        ) for filter_name, filter_values in selected_photometry.items()]

    redshift = target.redshift
    if redshift == None:
        redshift = 0
    if redshift > 0.01:
        ydata = []
        for filter_name, filter_values in selected_photometry.items():
            if filter_name is not None:
                ydata.append(np.asarray(filter_values['magnitude']) + np.asarray(filter_values['error']))
                ydata.append(np.asarray(filter_values['magnitude']) - np.asarray(filter_values['error']))
        if ydata:
            ydata = np.concatenate(ydata)
            ymin = np.min(ydata)
            ymax = np.max(ydata)
            ymin_view = ymin - 0.05 * (ymax-ymin)
            ymax_view = ymax + 0.05 * (ymax-ymin)
        else:
            ymin_view = 0
            ymax_view = 0

        dm = 5*np.log10(target.redshift*3e5/70.0*1e6) - 5
        yaxis2 = {'range': (ymax_view-dm, ymin_view-dm),
                  'showgrid': False,
                  'overlaying': 'y',
                  'side': 'right',
        }
        plot_data.append(go.Scatter(x=[], y=[], yaxis='y2'))

    else:
        yaxis2 = None

    graph_data = {'data': plot_data}

    layout = go.Layout(
        xaxis=dict(autorange='reversed',gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        yaxis=dict(autorange='reversed',gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        yaxis2=yaxis2,
        margin=dict(l=40, r=120, b=40, t=40),
        legend=dict(x=1.02, xanchor='left', y=1.0, bgcolor='rgba(0,0,0,0)'),
        autosize=True,
        height=height,
        hovermode='closest',
        plot_bgcolor='white',
        shapes=[
            dict(
                type='line',
                yref='paper',
                y0=0,
                y1=1,
                xref='x',
                x0=(datetime.now(timezone.utc) - s.timestamp.replace(tzinfo=timezone.utc)).total_seconds()/(24*3600),
                x1=(datetime.now(timezone.utc) - s.timestamp.replace(tzinfo=timezone.utc)).total_seconds()/(24*3600),
                opacity=0.2,
                line=dict(color='black', dash='dash'),
            ) for s in spec] + [{'type': 'line', 'yref': 'paper', 'y0': 0, 'y1': 1, 'xref': 'x',
                                 'x0': 0.0, 'x1': 0.0, 'opacity': 0.001,
                                 'line': {'color': 'black', 'dash': 'dash'}
                            }] #Have to put this in so plotly doesn't autofit the axes after zoom
    )

    ### Set the minimum x-axis range to one day
    min_xs = [min(filter_values['time']) for filter_values in selected_photometry.values()]

    if len(min_xs) > 0:# and len(max_xs) > 0:
        layout['xaxis']['range'] = [(datetime.now(timezone.utc) - min(min_xs).replace(tzinfo=timezone.utc)).total_seconds()/(24*3600)*1.06, 0]
        layout['xaxis']['autorange'] = False
        layout['xaxis']['title'] = 'Days Ago'

    graph_data['layout'] = layout

    return graph_data
