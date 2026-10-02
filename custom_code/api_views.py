import json
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from guardian.shortcuts import get_objects_for_user
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.mixins import UpdateModelMixin
from rest_framework.response import Response
from tom_dataproducts.api_views import DataProductViewSet, ReducedDatumViewSet
from tom_dataproducts.exceptions import InvalidFileFormatException
from tom_dataproducts.models import DataProduct
from tom_targets.api_views import TargetViewSet
from tom_targets.models import Target, TargetName

from custom_code.filters import SNExReducedDatumFilter
from custom_code.models import ReducedDatumExtra
from custom_code.processors.data_processor import run_custom_data_processor
from custom_code.processors.spectroscopy_processor import SpecProcessor
from custom_code.scheduling import save_comments
from custom_code.serializers import SNExReducedDatumSerializer, SNExTargetSerializer
from custom_code.utils import groups_from_payload, set_dataproduct_view_groups

logger = logging.getLogger(__name__)


class SNExTargetViewSet(TargetViewSet):
    serializer_class = SNExTargetSerializer

    def get_serializer(self, *args, **kwargs):
        self._serializer = super().get_serializer(*args, **kwargs)
        return self._serializer

    def create(self, request, *args, **kwargs):
        try:
            response = super().create(request, *args, **kwargs)
            if response.status_code == status.HTTP_201_CREATED and request.data.get('comment'):
                save_comments(str(request.data['comment']), response.data['id'], request.user, model_name='targets')
            return response
        except ValidationError:
            duplicate = getattr(self._serializer, 'duplicate', None) or Target.matches.find_duplicate(
                str(request.data.get('name') or ''), request.data.get('ra'), request.data.get('dec'),
                standard=bool(request.data.get('standard', False)))
            if duplicate is None:
                raise
        except IntegrityError:
            return Response({'name': ['A target with this name or alias already exists with a different standard flag.']},
                            status=status.HTTP_400_BAD_REQUEST)

        target, matched_by = duplicate
        alias_added = None
        new_name = str(request.data.get('name') or '').strip()
        if matched_by == 'position' and new_name:
            TargetName.objects.get_or_create(target=target, name=new_name)
            alias_added = new_name
            logger.info(f'Added alias {new_name} to target {target.id} ({target.name}) via API position match')

        return Response({'id': target.id, 'name': target.name, 'standard': target.standard, 'matched_by': matched_by,
                         'alias_added': alias_added, 'created': False,
                         'message': 'Target already exists.'},
                        status=status.HTTP_200_OK)

class SNExReducedDatumViewSet(UpdateModelMixin, ReducedDatumViewSet):
    serializer_class = SNExReducedDatumSerializer
    filterset_class = SNExReducedDatumFilter

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_superuser or settings.TARGET_PERMISSIONS_ONLY:
            return queryset
        viewable = get_objects_for_user(self.request.user, 'tom_dataproducts.view_reduceddatum', klass=queryset)
        return queryset.filter(Q(pk__in=viewable.values('pk')) | Q(data_type='photometric_standard'))

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            return Response({'detail': 'Only admins can delete reduced datums.'}, status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)


class SNExDataProductViewSet(DataProductViewSet):

    def create(self, request, *args, **kwargs):
        data = request.data.dict() if hasattr(request.data, 'dict') else dict(request.data)
        raw = data.get('data_product_type') == 'raw_spectrum'
        file, thumbnail = request.FILES.get('file'), request.FILES.get('thumbnail')
        if file:
            data['data'] = file
        elif not raw:
            return Response({'file': 'This field is required.'}, status=status.HTTP_400_BAD_REQUEST)
        groups = data.pop('groups', [])
        try:
            groups = groups_from_payload(json.loads(groups) if isinstance(groups, str) else groups)
        except (ValueError, AttributeError, TypeError):
            return Response({'groups': ['Expected a list of {"name": ...} objects.']}, status=status.HTTP_400_BAD_REQUEST)
        if str(data.get('target')).isdigit() and Target.objects.filter(pk=data.get('target'), standard=True).exists() and data.get('data_product_type') in (
                'raw_spectrum', 'spectroscopy'):
            return Response({'target': ['Spectra of standards are not stored in SNEx.']}, status=status.HTTP_400_BAD_REQUEST)

        posted = {key: data[key] for key in SpecProcessor.field_keywords if data.get(key)}
        try:
            posted.update({key: float(posted[key]) for key in ('exptime', 'slit', 'airmass') if key in posted})
        except ValueError as e:
            return Response({'extras': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        product_id = data.get('product_id')
        existing = DataProduct.objects.filter(product_id=product_id).first() if product_id else None
        if existing and str(existing.target_id) != str(data.get('target')):
            return Response({'product_id': f'{product_id} already belongs to target {existing.target_id}'},
                            status=status.HTTP_400_BAD_REQUEST)
        if raw and existing:
            return Response({'id': existing.id, 'product_id': product_id, 'already_posted': True,
                             'already_reduced': existing.reduceddatum_set.exists()}, status=status.HTTP_200_OK)

        with transaction.atomic():
            if existing:
                dp = existing
                replaced = dp.reduceddatum_set.exists()
                dp.reduceddatum_set.all().delete()
                ReducedDatumExtra.objects.filter(data_product=dp).delete()
                dp.data_product_type = data['data_product_type']
                if file:
                    dp.data = file
                if data.get('extra_data'):
                    dp.extra_data = data['extra_data']
                dp.save()
            else:
                replaced = False
                serializer = self.get_serializer(data=data)
                serializer.is_valid(raise_exception=True)
                dp = serializer.save()
            if thumbnail:
                dp.thumbnail.save(thumbnail.name, thumbnail)

            reduceddatums, rd_extras = [], {}
            if not raw:
                try:
                    reduced_data, rd_extras = run_custom_data_processor(dp, {}, {'data_product_id': dp.id, **posted})
                except Exception as e:
                    transaction.set_rollback(True)
                    if isinstance(e, InvalidFileFormatException):
                        return Response({'file': f'Invalid file format: {e}'}, status=status.HTTP_400_BAD_REQUEST)
                    logger.exception(f'Processing failed for uploaded data product {dp.data.name}')
                    return Response({'file': f'Could not process file: {e}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                reduceddatums = list(reduced_data.values_list('id', flat=True))
                ReducedDatumExtra.objects.create(target_id=dp.target_id, data_product=dp, data_type=dp.data_product_type,
                                                 key='upload_extras', value=rd_extras)
                if dp.data_product_type == 'spectroscopy':
                    DataProduct.objects.filter(pk=dp.pk).update(created=reduced_data.first().timestamp)
            if not settings.TARGET_PERMISSIONS_ONLY:
                set_dataproduct_view_groups(dp, groups)

        return Response({'id': dp.id, 'product_id': dp.product_id, 'target': dp.target_id,
                         'data': dp.data.name or None, 'thumbnail': dp.thumbnail.name or None,
                         'reduceddatums': reduceddatums, 'extras': rd_extras,
                         'groups': sorted(g.name for g in groups), 'replaced': replaced},
                        status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED)
