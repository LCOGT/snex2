import os
from importlib import import_module
from django.conf import settings
from django.utils import timezone
from tom_dataproducts.models import try_parse_reduced_datum
from tom_targets.sharing import continuous_share_data

from custom_code.utils import file_version, measured, upload_reduction_version

DEFAULT_DATA_PROCESSOR_CLASS = 'tom_dataproducts.data_processor.DataProcessor'

def run_custom_data_processor(dp, extras, rd_extras, uploaded_by=''):
    try:
        processor_class = settings.DATA_PROCESSORS[dp.data_product_type]
    except Exception:
        processor_class = DEFAULT_DATA_PROCESSOR_CLASS

    try:
        mod_name, class_name = processor_class.rsplit('.', 1)
        mod = import_module(mod_name)
        clazz = getattr(mod, class_name)
    except (ImportError, AttributeError):
        raise ImportError('Could not import {}. Did you provide the correct path?'.format(processor_class))
    data_processor = clazz()
    data, rd_extras = data_processor.process_data(dp, extras, rd_extras)

    version, fields = upload_reduction_version(dp.id), {}
    if dp.data_product_type == 'spectroscopy':
        version = file_version(dp.data)
        fields = {'telescope': str(rd_extras.get('telescope') or ''), 'instrument': str(rd_extras.get('instrument') or ''),
                  'exposure_time': measured(rd_extras.get('exptime')), 'reducer': rd_extras.get('reducer') or '',
                  'final_reduction': bool(rd_extras.get('final_reduction')), 'uploaded_by': uploaded_by,
                  'uploaded': timezone.now().isoformat()}
        rd_extras['file_version'] = version

    reduced_datums = [try_parse_reduced_datum({
        'target': dp.target, 'data_product': dp, 'data_type': dp.data_product_type, 'timestamp': datum[0],
        'reduction_version': version, **fields, 'value': datum[1]}) for datum in data]
    model = type(reduced_datums[0])
    posted = model.objects.filter(target=dp.target, reduction_version=version)
    if dp.data_product_type == 'spectroscopy' and posted.exists():
        return posted, rd_extras
    reduced_datums = model.objects.bulk_create(reduced_datums)
    continuous_share_data(dp.target, reduced_datums)

    return model.objects.filter(data_product=dp, reduction_version=version), rd_extras


def merge_into_observation(dp, datums):
    spectrum = datums.first()
    if dp.data_product_type != 'spectroscopy' or spectrum is None:
        return dp, datums
    if spectrum.data_product_id != dp.id:
        dp.delete()
        return spectrum.data_product, datums
    same_time = type(spectrum).objects.filter(target=dp.target, timestamp=spectrum.timestamp).exclude(
        data_product=dp).exclude(data_product=None)
    observation = next((other.data_product for other in same_time
                        if not other.instrument or not spectrum.instrument or other.instrument == spectrum.instrument), None)
    if observation is None:
        return dp, datums
    versions = type(spectrum).objects.filter(data_product=observation)
    if not versions.filter(reduction_version=spectrum.reduction_version).exists():
        datums.update(data_product=observation)
        observation.data.save(os.path.basename(dp.data.name), dp.data.file)
        for extra in observation.reduceddatumextra_set.all():
            extra.value['file_version'] = spectrum.reduction_version
            extra.save()
    dp.delete()
    return observation, versions.filter(reduction_version=spectrum.reduction_version)
