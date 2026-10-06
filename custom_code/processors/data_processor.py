from importlib import import_module
from django.conf import settings
from tom_dataproducts.models import try_parse_reduced_datum
from tom_targets.sharing import continuous_share_data

from custom_code.utils import upload_reduction_version

DEFAULT_DATA_PROCESSOR_CLASS = 'tom_dataproducts.data_processor.DataProcessor'

def run_custom_data_processor(dp, extras, rd_extras):
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

    reduced_datums = [try_parse_reduced_datum({
        'target': dp.target, 'data_product': dp, 'data_type': dp.data_product_type, 'timestamp': datum[0],
        'reduction_version': upload_reduction_version(dp.id), 'value': datum[1]}) for datum in data]
    model = type(reduced_datums[0])
    reduced_datums = model.objects.bulk_create(reduced_datums)
    continuous_share_data(dp.target, reduced_datums)

    return model.objects.filter(data_product=dp), rd_extras
