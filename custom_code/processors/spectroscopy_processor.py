import mimetypes
from tom_dataproducts.processors.spectroscopy_processor import SpectroscopyProcessor
from tom_dataproducts.exceptions import InvalidFileFormatException
from tom_dataproducts.processors.data_serializers import SpectrumSerializer
from tom_observations.facility import get_service_class, get_service_classes
from django.core.files.storage import default_storage
from astropy.io import fits, ascii
from astropy.wcs import WCS
from astropy.time import Time
from astropy import units
from specutils import Spectrum1D
from datetime import datetime
import numpy as np

class SpecProcessor(SpectroscopyProcessor):

    FITS_MIMETYPES = ['image/fits', 'application/fits']
    PLAINTEXT_MIMETYPES = ['text/plain', 'text/csv', 'text/ascii']
    DEFAULT_FLUX_CONSTANT = (1 * units.erg) / units.cm ** 2 / units.second / units.angstrom
    field_keywords = {
        "objname": ["object", "objname", "target"],
        "date_obs": ["mjd", "mjd-obs", "mjd_obs", "mjdobs", "obsmjd",
                     "jd", "jd-obs", "jd_obs", "jdobs", "obsjd",
                     "date-obs", "dateobs", "obs-date", "obsdate",
                     "utshut", "utc-obs", "utc"],        
        "telescope": ["telescope", "telescop", "observat"],
        "instrument": ["instrument", "instrume"],
        "slit": ["APERWID", "slit", "aperture", "slitname"],
        "exptime": ["exptime", "exposure", "itot"],
        "airmass": ["airmass", "am", "tcs_am"],
        "grism": ["grism"],
        "observer": ["observer"],
        "reducer": ["reducer", "reducedby"],
    }

    def process_data(self, data_product, extras, dp_extras):
        mimetype = mimetypes.guess_type(data_product.data.name)[0]
        if mimetype in self.FITS_MIMETYPES:
            spectrum, obs_date, dp_extras = self._process_spectrum_from_fits(data_product, dp_extras)
        elif mimetype in self.PLAINTEXT_MIMETYPES:
            spectrum, obs_date, dp_extras = self._process_spectrum_from_plaintext(data_product, dp_extras)
        else:
            try:
                spectrum, obs_date, dp_extras = self._process_spectrum_from_plaintext(data_product, dp_extras)
            except:
                raise InvalidFileFormatException('Unsupported file type')
        serialized_spectrum = SpectrumSerializer().serialize(spectrum)

        return [(obs_date, serialized_spectrum)], dp_extras


    def _process_spectrum_from_fits(self, data_product, dp_extras):

        data_aws = default_storage.open(data_product.data.name, 'rb')

        spectrum, dp_extras, date_obs = self.process_fits_file(data_aws.open(), dp_extras)

        return spectrum, date_obs, dp_extras


    def _process_spectrum_from_plaintext(self, data_product, dp_extras):
        """
        Processes the data from a spectrum from a plaintext file into a Spectrum1D object, which can then be serialized
        and stored as a ReducedDatum for further processing or display. File is read using astropy as specified in
        the below documentation.
        # http://docs.astropy.org/en/stable/io/ascii/read.html

        Parameters
        ----------
        :param data_product: Spectroscopic DataProduct which will be processed into a Spectrum1D
        :type data_product: tom_dataproducts.models.DataProduct

        :returns: Spectrum1D object containing the data from the DataProduct
        :rtype: specutils.Spectrum1D

        :returns: Datetime of observation, if it is in the comments and the file is from a supported facility, current
            datetime otherwise
        :rtype: AstroPy.Time
        """

        data = ascii.read(data_product.data.path)

        if 'flux' in data.colnames and 'wavelength' in data.colnames:
            pass
        elif 'wavelength' in data.colnames and 'flux' not in data.colnames:
            data.rename_column(data.colnames[1], 'flux')
        elif data.colnames == ['col1', 'col2']:
            data.rename_column('col1', 'wavelength')
            data.rename_column('col2', 'flux')
        elif data.colnames == ['col2', 'col1'] or (len(data.colnames) == 2 and 'col' in data.colnames[0]):
            data.rename_column(data.colnames[0], 'wavelength')
            data.rename_column(data.colnames[1], 'flux')
        else:
            raise InvalidFileFormatException('Could not determine wavelength/flux columns')
        
        if len(data) < 1:
            raise InvalidFileFormatException('Empty table or invalid file type')
        facility_name = None

        date_obs = dp_extras.get('date_obs', None)

        comments = data.meta.get('comments', [])

        for comment in comments:
            if '=' in comment:
                delim = '='
            else:
                delim = ':'
            parts = comment.split(delim)
            if len(parts) < 2:
                continue

            keyword = parts[0].strip().lower()
            value = parts[1].strip()
            if not date_obs and 'date-obs' in comment.lower():
                date_obs = value.split('/')[0].strip()
            else:
                date_obs = datetime.now()

            if 'facility' in comment.lower():
                facility_name = value

            keyword = comment.split(delim)[0].lower()
            if keyword in dp_extras.keys() and not dp_extras.get(keyword, ''):
                dp_extras[keyword] = value

        facility = get_service_class(facility_name)() if facility_name else None
        wavelength_units = facility.get_wavelength_units() if facility else self.DEFAULT_WAVELENGTH_UNITS
        flux_constant = facility.get_flux_constant() if facility else self.DEFAULT_FLUX_CONSTANT

        spectral_axis = np.array(data['wavelength']) * wavelength_units
        flux = np.array(data['flux']) * flux_constant
        spectrum = Spectrum1D(flux=flux, spectral_axis=spectral_axis)
        dp_extras.pop('date_obs')

        return spectrum, Time(date_obs).to_datetime(), dp_extras


def process_fits_file(file, dp_extras):
    hlist = fits.open(file)
    banzai_reduc = 'SPECTRUM' in hlist
    if banzai_reduc:
            header = hlist['PRIMARY'].header
            spec_table = hlist['SPECTRUM'].data
            flux = spec_table['flux']
            wav = spec_table['wavelength']
    else:
        flux, header = fits.getdata(file, header=True)

    for facility_class in get_service_classes():
        facility = get_service_class(facility_class)()
        if facility.is_fits_facility(header):
            flux_constant = facility.get_flux_constant()
            if dp_extras.get('date_obs'):
                #logger.info(f"dp_extras date obs: {dp_extras['date_obs']}")
                date_obs = datetime.fromisoformat(str(dp_extras['date_obs']).replace(' ', 'T'))
            else:
                date_obs = facility.get_date_obs_from_fits_header(header)
            break
    else:
        flux_constant = SpecProcessor.DEFAULT_FLUX_CONSTANT
        if dp_extras.get('date_obs'):
            #logger.info(f"dp_extras date obs in else statement: {dp_extras['date_obs']}")
            date_obs = datetime.fromisoformat(str(dp_extras['date_obs']).replace(' ', 'T'))
        else:
            date_obs = Time(datetime.now()).to_datetime
    
    for keyword, possibles in SpecProcessor.field_keywords.items():


        # Check if the keyword or any possible is already in dp_extras with a non-empty value; if so, skip this keyword
        if (keyword in dp_extras and dp_extras.get(keyword, '')) or any(possible in dp_extras and dp_extras.get(possible, '') for possible in possibles):
            continue

        # If none are in dp_extras, check the header for each possible
        for possible in possibles:
            if possible in header:
                value = header[possible]
                if keyword == "date_obs" and not dp_extras.get('date_obs'):
                    k_lower = possible.lower()
                    if "mjd" in k_lower:
                        value = Time(float(value), format="mjd").to_datetime()
                    elif "jd" in k_lower:
                        value = Time(float(value), format="jd").to_datetime()
                    else:
                        value = datetime.fromisoformat(str(value).replace(' ', 'T'))
                    date_obs = value
                dp_extras[keyword] = value
                break

    if not banzai_reduc:
        dim = len(flux.shape)
        if dim == 3:
            flux = flux[0, 0, :]
        elif flux.shape[0] == 2:
            flux = flux[0, :]
        flux = flux * flux_constant
        header['CUNIT1'] = 'Angstrom'

    
        wcs = WCS(header=header, naxis=1)
        spectrum = Spectrum1D(flux=flux, wcs=wcs)
    else:
        flux_constant = SpecProcessor.DEFAULT_FLUX_CONSTANT
        # Convert flux and wavelength to arrays and skip NaNs
        flux_values = np.array(flux, dtype=float)
        wav_values = np.array(wav, dtype=float)
        valid_mask = ~np.isnan(flux_values)  # keep only non-NaN flux points
        spectrum = Spectrum1D(flux=flux_values[valid_mask] * flux_constant, spectral_axis=wav_values[valid_mask] * units.Angstrom)
        #spectrum = Spectrum1D(flux=flux, spectral_axis=np.array(wav) * units.Angstrom)
    dp_extras.pop('date_obs')
    return spectrum, dp_extras, date_obs
    
