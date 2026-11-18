from django.apps import AppConfig
from django.db.models import CharField
from django.db.models.functions import Cast
from django.db.models.signals import post_delete, pre_delete


def delete_target_related_objects(sender, instance, **kwargs):
    from django.contrib.contenttypes.models import ContentType
    from django_comments.models import Comment
    from guardian.models import GroupObjectPermission, UserObjectPermission
    from tom_dataproducts.models import REDUCED_DATUM_MODELS, DataProduct
    from tom_observations.models import DynamicCadence, ObservationRecord
    DynamicCadence.objects.filter(observation_group__observation_records__target=instance).update(active=False)
    querysets = [type(instance).objects.filter(pk=instance.pk)]
    querysets += [model.objects.filter(target=instance) for model in (DataProduct, ObservationRecord, *REDUCED_DATUM_MODELS)]
    for queryset in querysets:
        content_type = ContentType.objects.get_for_model(queryset.model)
        object_pks = queryset.annotate(object_pk=Cast('pk', CharField())).values('object_pk')
        for model in (UserObjectPermission, GroupObjectPermission, Comment):
            model.objects.filter(content_type=content_type, object_pk__in=object_pks).delete()


class CustomPlotsConfig(AppConfig):
    name = 'custom_code'

    def ready(self):
        from tom_targets.models import Target
        from tom_targets.signals.handlers import cb_target_post_delete
        post_delete.disconnect(cb_target_post_delete, sender=Target)
        pre_delete.connect(delete_target_related_objects, sender=Target)
