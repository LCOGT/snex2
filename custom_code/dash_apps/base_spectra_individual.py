import dash
from dash.dependencies import Input, Output, State
import dash_bootstrap_components as dbc
import dash_core_components as dcc
import dash_html_components as html
import plotly.graph_objs as go
from statistics import median
import logging

from django_plotly_dash import DjangoDash
from tom_dataproducts.models import ReducedDatum, DataProduct
from custom_code.templatetags.custom_code_tags import bin_spectra
from django.templatetags.static import static
from custom_code.dash_apps.spectra_utils import calculate_flux_range

logger = logging.getLogger(__name__)

app = DjangoDash(name='Base_Spectra_Individual', add_bootstrap_links=True, suppress_callback_exceptions=True)
app.css.append_css({'external_url': static('custom_code/css/dash.css')})

app.layout = html.Div([
    dcc.Graph(id='spectra-plot',
              figure={
                  'layout': {
                      'height': 350,
                      'margin': {'l': 60, 'b': 30, 'r': 60, 't': 10},
                      'yaxis': {'type': 'linear', 'tickformat': '.1e'},
                      'xaxis': {'showgrid': False},
                      'legend': {'x': 0.85, 'y': 1.0},
                  },
                  'data': []
              }
    ),
    dcc.Input(id='spectrum_id', type='hidden', value=0),
    dcc.Input(id='target_id', type='hidden', value=0),
    html.Div([
        html.Label('Compare to version:', style={'fontSize': 14, 'marginRight': '10px'}),
        dcc.Dropdown(
            id='version-compare-dropdown',
            options=[],
            value=None,
            placeholder='Select a version to compare...',
            searchable=False,  # removes the search bar
            style={'width': '400px', 'display': 'inline-block', 'fontSize': 14}
        ),
    ], style={'display': 'flex', 'alignItems': 'center', 'marginTop': '8px'}),
], style={'padding': '0px'})


def extract_spectrum(datum):
    """Pull wavelength and flux arrays out of a ReducedDatum."""
    wavelength, flux = [], []
    value = datum.value
    if value.get('photon_flux'):
        wavelength = value.get('wavelength')
        flux = value.get('photon_flux')
    elif value.get('flux'):
        wavelength = value.get('wavelength')
        flux = value.get('flux')
    else:
        for key, v in value.items():
            wavelength.append(float(v['wavelength']))
            flux.append(float(v['flux']))
    return wavelength, flux


@app.expanded_callback(
    Output('version-compare-dropdown', 'options'),
    [Input('spectrum_id', 'value')]
)
def populate_version_dropdown(spectrum_id, *args, **kwargs):
    if not spectrum_id or spectrum_id == 0:
        return []
    try:
        spectrum = ReducedDatum.objects.get(id=spectrum_id)
        if not spectrum.data_product:
            return []
        siblings = ReducedDatum.objects.filter(
            data_product=spectrum.data_product,
            data_type='spectroscopy'
        ).exclude(
            id=spectrum_id
        ).exclude(
            value__fits_data__isnull=False  # exclude 2D datums
        ).order_by('-timestamp')
        return [
            {'label': f"{rd.timestamp.strftime('%Y-%m-%d %H:%M')} (id: {rd.id})", 'value': rd.id}
            for rd in siblings
        ]
    except ReducedDatum.DoesNotExist:
        return []

@app.expanded_callback(
    Output('spectra-plot', 'figure'),
    [Input('spectrum_id', 'value'),
     Input('version-compare-dropdown', 'value')],
    [State('spectra-plot', 'figure')]
)
def update_plot(spectrum_id, compare_id, current_figure, *args, **kwargs):
    if not spectrum_id or spectrum_id == 0:
        return current_figure

    layout = current_figure['layout']
    traces = []

    try:
        # Always plot the primary spectrum
        primary = ReducedDatum.objects.get(id=spectrum_id)
        wavelength, flux = extract_spectrum(primary)
        traces.append(go.Scatter(
            x=wavelength,
            y=flux,
            name=f"Primary ({primary.timestamp.strftime('%Y-%m-%d')})",
            line={'color': 'black'}
        ))

        # Plot comparison spectrum if selected
        if compare_id:
            compare = ReducedDatum.objects.get(id=compare_id)
            wav2, flux2 = extract_spectrum(compare)
            # Normalize both to their medians for comparison
            if flux and flux2:
                norm1 = [f / median(flux) for f in flux]
                norm2 = [f / median(flux2) for f in flux2]
                traces[0]['y'] = norm1
                traces.append(go.Scatter(
                    x=wav2,
                    y=norm2,
                    name=f"Compare ({compare.timestamp.strftime('%Y-%m-%d')})",
                    line={'color': 'steelblue', 'dash': 'dash'}
                ))

    except ReducedDatum.DoesNotExist:
        logger.error('ReducedDatum not found: %s or %s', spectrum_id, compare_id)

    return {'data': traces, 'layout': layout}