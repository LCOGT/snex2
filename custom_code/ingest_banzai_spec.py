import requests
import json
import tempfile
import os
from datetime import datetime
from io import BytesIO

import numpy as np
from astropy.io import fits
from astropy import units as u
from specutils import Spectrum1D
import logging

from tom_dataproducts.processors.data_serializers import SpectrumSerializer
from tom_dataproducts.models import ReducedDatum, DataProduct, ObservationRecord
from tom_targets.models import Target, TargetName
from custom_code.models import ReducedDatumExtra 
from custom_code.processors.data_processor import run_custom_data_processor

logger = logging.getLogger(__name__)

# ── 1. FETCH FRAME METADATA FROM THE ARCHIVE ────────────────────────────────

token = os.environ['LCO_APIKEY']
authtoken = {'Authorization': 'Token ' + token}

def get_metadata(authtoken={}, limit=None, **kwargs):
    url = 'https://archive-api.lco.global/frames/?' + '&'.join(
        [key + '=' + str(val) for key, val in kwargs.items() if val is not None])
    url = url.replace('False', 'false').replace('True', 'true')
    response = requests.get(url, headers=authtoken, stream=True).json()
    frames = response['results']
    while response['next'] and (limit is None or len(frames) < limit):
        response = requests.get(response['next'], headers=authtoken, stream=True).json()
        frames += response['results']
    return frames[:limit]


# ── 2. DOWNLOAD A FRAME INTO MEMORY ─────────────────────────────────────────

def download_frame(frame, authtoken={}):
    """Download a frame's FITS data into a BytesIO buffer."""
    url = frame['url']
    response = requests.get(url, headers=authtoken, stream=True)
    response.raise_for_status()
    return BytesIO(response.content)


# ── 3. READ THE FITS AND BUILD A Spectrum1D ──────────────────────────────────
# FLOYDS reduced spectra (from banzai-floyds) are multi-extension FITS.
# The 1D extracted spectrum lives in a FITS table extension.

def spectrum1d_from_floyds_fits(fits_buffer):
    """
    Parse a FLOYDS FITS file from the LCO archive into a Spectrum1D.
    The extracted spectrum table has columns WAVELENGTH, FLUX (and optionally FLUXERROR).
    """
    with fits.open(fits_buffer) as hdul:
        # banzai-floyds puts the 1D spectrum in an extension named 'SPECTRUM'
        # Fall back to extension 1 if not found
        try:
            spec_ext = hdul['SPECTRUM']
        except KeyError:
            spec_ext = hdul[1]

        data = spec_ext.data
        header = spec_ext.header

        wavelength = data['WAVELENGTH'] * u.angstrom
        flux = data['FLUX'] * (u.erg / u.cm**2 / u.s / u.angstrom)

        # Uncertainty is optional but include it if present
        if 'FLUXERROR' in data.names:
            from astropy.nddata import StdDevUncertainty
            uncertainty = StdDevUncertainty(
                data['FLUXERROR'] * (u.erg / u.cm**2 / u.s / u.angstrom)
            )
            spectrum = Spectrum1D(
                spectral_axis=wavelength,
                flux=flux,
                uncertainty=uncertainty
            )
        else:
            spectrum = Spectrum1D(spectral_axis=wavelength, flux=flux)

    return spectrum


# ── 4. SAVE AS ReducedDatum (+ ReducedDatumExtra) ───────────────────────────

def ingest_spectrum_from_frame(frame, target, authtoken={}):
    """
    Given one frame dict from get_metadata() and a Target object,
    download the FITS, parse it, and save it to the ReducedDatum table.
    """
    #targetname = frame['target_name']
    
    # Download
    fits_buffer = download_frame(frame, authtoken=authtoken)


    

    # Parse to Spectrum1D
    spectrum = spectrum1d_from_floyds_fits(fits_buffer)

    # Serialize for the DB (this is what TOM Toolkit expects in ReducedDatum.value)
    serialized = SpectrumSerializer().serialize(spectrum)

    # Parse the observation timestamp from the frame metadata
    obs_date = datetime.strptime(frame['DATE_OBS'], '%Y-%m-%dT%H:%M:%S.%f')

    # --- Avoid duplicates ---
    # The archive basename is a reliable unique identifier
    existing = ReducedDatum.objects.filter(
        target=target,
        data_type='spectroscopy',
        source_name='',
        source_location=''
    ).first()
    if existing:
        print(f"Already ingested {frame['basename']}, skipping.")
        return existing


    # making dataproduct (filename for reduced spectrum, make product id be frame id, and extra_data have basename),
    # make reduceddatum - leave source_name and source_location blank
    #  get obs id, query ObservationRecord.objects.get(observation_id = requestID) - check which id you need to match to cadence strategy. Dataproduct takes observationrecord as parameter, frameid, filename. - if stop and restart w/ same parameters (delay start=0). pull obs record, get cadence info, call modify_sequence from scheduling.py. data comes from schedulingPhot form - use obs.parameters as data, make sure delay-start =0

    # download file, make dp, run spec processor: run_custom_data_processor, how to check for new spec??




    # Create the ReducedDatum
    rd = ReducedDatum.objects.create(
        target=target,
        data_product=None,          # no DataProduct file object — ingested directly
        data_type='spectroscopy',
        timestamp=obs_date,
        value=serialized,
        source_name=''
        source_location='',  # archive basename for traceability
    )

    # Create the snex2-specific ReducedDatumExtra for display metadata
    ReducedDatumExtra.objects.create(
        reduced_datum=rd,
        data_type='spectroscopy',
        key='spec_extras',
        value=json.dumps({
            'telescope':  frame.get('TELID', ''),
            'instrument': frame.get('INSTRUME', ''),
            'site':       frame.get('SITEID', ''),
            'exptime':    frame.get('EXPTIME', ''),
            'reducer':    'Banzai-Floyds',       # fill in if known
            'airmass':    frame.get('AIRMASS', ''),
        })
    )

    # adding extra flag for approval in inbox view -- can this go in spec_extras?
    ReducedDatumExtra.objects.create(
        reduced_datum=rd,
        data_type='spectroscopy',
        key='approval',
        value=json.dumps({
            'approval':   'None'
        })
    )

    print(f"Ingested {frame['basename']} → ReducedDatum id={rd.id}")
    return rd


# ── 5. TOP-LEVEL: QUERY + INGEST ALL MATCHING FRAMES ────────────────────────

def ingest_spectra_for_target(target_name, proposal_id, authtoken={}):
    target = Target.objects.get(name=target_name)
    #
    frames = get_metadata(
        authtoken=authtoken,
        OBJECT=target_name,
        PROPID=proposal_id,
        OBSTYPE='SPECTRUM',
        RLEVEL=2,           # reduction level 2 = fully reduced FLOYDS product
        limit=None
    )

    

    for frame in frames:

        #request_id = frame['request_id']

        targetquery = Target.objects.filter(name=frame['target_name'])
        logger.info(f"ingest_banzai_spec targetquery, targetname: {targetquery} , {frame['target_name']}")
        if not targetquery:
            targetquery = TargetName.objects.filter(name=frame['target_name'])
            logger.info(f"targetquery in if statement: {targetquery}")
            targetid = targetquery.first().target_id
            logger.info(f"in if statement: Targetid: {targetid}")
        else:
            logger.info(f"targetquery in else statement: {targetquery}")
            targetid = targetquery.first().id
            logger.info(f"in else statement Targetid: {targetid}")


        target = Target.objects.get(id=targetid)

        observation_id = frame['observation_id']
        obs_record = ObservationRecord.objects.get(observation_id = observation_id)
        
        dp = DataProduct(
                    target=target,
                    observation_record=obs_record,
                    product_id=frame['id'],
                    data_product_type='spectroscopy',
                    extra_data = frame['basename']
                )
        dp.save()
        extras = {} # only needed for photometry
        
        rdextra_value = {
            'telescope':  frame.get('TELID', ''),
            'instrument': frame.get('INSTRUME', ''),
            'site':       frame.get('SITEID', ''),
            'exptime':    frame.get('EXPTIME', ''),
            'reducer':    'Banzai-Floyds',       # fill in if known -- how to change if reduced manually??
            'airmass':    frame.get('AIRMASS', '')
        }

        reduced_data, rdextra_value = run_custom_data_processor(dp, extras, rdextra_value) # uses spectroscopy_processor to make ReducedDatum objects
        try:
            ingest_spectrum_from_frame(frame, target, authtoken=authtoken)
        except Exception as e:
            print(f"Failed on {frame['basename']}: {e}")


# # ── USAGE ────────────────────────────────────────────────────────────────────
# if __name__ == '__main__':
#     authtoken = {'Authorization': 'Token YOUR_LCO_ARCHIVE_TOKEN'}
#     ingest_spectra_for_target('SN2024abc', 'KEY2024A-001', authtoken=authtoken)
# Use get_unreduced_spectra function from hooks.py to get all frames required, then use ingest_spectrum_from_frame to add it to the ReducedDatum table and spec (snex1 db). run on cron job