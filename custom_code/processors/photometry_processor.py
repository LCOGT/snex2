import math
import mimetypes

from astropy import units
from astropy.io import ascii
from astropy.time import Time, TimezoneInfo
from django.core.files.storage import default_storage

from tom_dataproducts.data_processor import DataProcessor
from tom_dataproducts.exceptions import InvalidFileFormatException


class PhotometryProcessor(DataProcessor):

    def process_data(self, data_product, extras, rd_extras):

        mimetype = mimetypes.guess_type(data_product.data.name)[0]
        if mimetype in self.PLAINTEXT_MIMETYPES:
            photometry, rd_extras = self._process_photometry_from_plaintext(data_product, extras, rd_extras)
            return [(datum.pop('timestamp'), datum) for datum in photometry], rd_extras
        else:
            raise InvalidFileFormatException('Unsupported file type')

    def _process_photometry_from_plaintext(self, data_product, extras, rd_extras):

        photometry = []

        data_aws = default_storage.open(data_product.data.name, 'r')
        data = ascii.read(data_aws.read(),
                          names=['time', 'filter', 'magnitude', 'error'])

        if len(data) < 1:
            raise InvalidFileFormatException('Empty table or invalid file type')

        comments = data.meta.get('comments', [])
        for comment in comments:
            if '=' in comment:
                delim = '='
            else:
                delim = ':'

            keyword = comment.split(delim)[0].lower()
            if keyword in rd_extras.keys() and not rd_extras.get(keyword, ''):
                rd_extras[keyword] = comment.split(delim)[1].strip()

        for datum in data:
            time = Time(float(datum['time']), format='mjd')
            utc = TimezoneInfo(utc_offset=0*units.hour)
            time.format = 'datetime'
            value = {
                'timestamp': time.to_datetime(timezone=utc),
                'magnitude': float(datum['magnitude']) if math.isfinite(float(datum['magnitude'])) else None,
                'filter': str(datum['filter']),
                'error': float(datum['error']) if math.isfinite(float(datum['error'])) else None
            }
            value.update(extras)

            photometry.append(value)

        return photometry, rd_extras

