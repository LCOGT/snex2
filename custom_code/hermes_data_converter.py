import json

from tom_dataproducts.alertstreams.hermes import HermesDataConverter
from custom_code.models import ReducedDatumExtra


class SNEx2HermesDataConverter(HermesDataConverter):
    def get_hermes_spectroscopy(self, datum):
        spectroscopy_row = super().get_hermes_spectroscopy(datum)
        reduced_datum_extra = ReducedDatumExtra.objects.filter(
            data_type='spectroscopy', key='spec_extras', reduced_datum = datum).first()
        if reduced_datum_extra:
            extra_data = json.loads(reduced_datum_extra.value)
            if 'telescope' in extra_data:
                spectroscopy_row['telescope'] = extra_data.pop('telescope')
            if 'instrument' in extra_data:
                spectroscopy_row['instrument'] = extra_data.pop('instrument')
            if 'exptime' in extra_data:
                spectroscopy_row['exposure_time'] = extra_data.pop('exptime')
            if 'reducer' in extra_data:
                spectroscopy_row['reducer'] = extra_data.pop('reducer')
            del extra_data['snex_id']
            comment = ''
            for i, (key, item) in enumerate(extra_data.items()):
                comment += f'{key}: {item}'
                if i < (len(extra_data) - 1):
                    comment += ', '
            if comment:
                spectroscopy_row['comments'] = comment
        return spectroscopy_row
