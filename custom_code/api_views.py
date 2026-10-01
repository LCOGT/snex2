import json
import logging

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.mixins import UpdateModelMixin
from rest_framework.response import Response
from tom_dataproducts.api_views import DataProductViewSet, ReducedDatumViewSet
from tom_dataproducts.exceptions import InvalidFileFormatException
from tom_dataproducts.models import DataProduct
from tom_targets.api_views import TargetViewSet
from tom_targets.models import TargetName

from custom_code.filters import SNExReducedDatumFilter, SNExTargetFilterSet
from custom_code.models import ReducedDatumExtra
from custom_code.processors.data_processor import run_custom_data_processor
from custom_code.serializers import SNExReducedDatumSerializer, SNExTargetSerializer
from custom_code.utils import set_dataproduct_view_groups

logger = logging.getLogger(__name__)


class SNExTargetViewSet(TargetViewSet):
    serializer_class = SNExTargetSerializer
    filterset_class = SNExTargetFilterSet

    def get_serializer(self, *args, **kwargs):
        self._serializer = super().get_serializer(*args, **kwargs)
        return self._serializer

    def create(self, request, *args, **kwargs):
        """Get-or-create: 201 new; 200 existing (position-only match adds the POSTed name as an alias)."""
        try:
            return super().create(request, *args, **kwargs)
        except ValidationError:
            duplicate = getattr(self._serializer, 'duplicate', None)
            if duplicate is None:
                raise

        target, matched_by = duplicate
        alias_added = None
        new_name = str(request.data.get('name') or '').strip()
        if matched_by == 'position' and new_name:
            TargetName.objects.get_or_create(target=target, name=new_name)
            alias_added = new_name
            logger.info(f'Added alias {new_name} to target {target.id} ({target.name}) via API position match')

        return Response({'id': target.id, 'name': target.name, 'matched_by': matched_by,
                         'alias_added': alias_added, 'created': False,
                         'message': 'Target already exists.'},
                        status=status.HTTP_200_OK)

class SNExReducedDatumViewSet(UpdateModelMixin, ReducedDatumViewSet):
    serializer_class = SNExReducedDatumSerializer
    filterset_class = SNExReducedDatumFilter

    def update(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            return Response({'detail': 'Only admins can update reduced datums.'}, status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)


class SNExDataProductViewSet(DataProductViewSet):

    def create(self, request, *args, **kwargs):
        """
        Multipart upload of one file (`file`) for `target`, processed with the SNEx2 data processors.
        `groups` is a JSON list like [{"name": "gsp"}] of groups that can view it. A `product_id` that
        already exists for the same target is replaced (re-reduction of the same raw frame).
        """
        data = request.data
        data['data'] = request.FILES['file']
        group_names = [g.get('name') for g in json.loads(data.pop('groups', ['[]'])[0])]
        groups = list(Group.objects.filter(name__in=group_names))
        missing = set(group_names) - {g.name for g in groups}
        if missing:
            return Response({'groups': f'Unknown groups: {sorted(missing)}'}, status=status.HTTP_400_BAD_REQUEST)

        product_id = data.get('product_id')
        existing = DataProduct.objects.filter(product_id=product_id).first() if product_id else None
        if existing and str(existing.target_id) != str(data.get('target')):
            return Response({'product_id': f'{product_id} already belongs to target {existing.target_id}'},
                            status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():  # a failed re-upload keeps the existing data product
            if existing:
                existing.delete()
            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)
            dp = serializer.save()
            try:
                reduced_data, rd_extras = run_custom_data_processor(dp, {}, {'data_product_id': dp.id})
            except Exception as e:
                transaction.set_rollback(True)
                if isinstance(e, InvalidFileFormatException):
                    return Response({'file': f'Invalid file format: {e}'}, status=status.HTTP_400_BAD_REQUEST)
                logger.exception(f'Processing failed for uploaded data product {dp.data.name}')
                return Response({'file': f'Could not process file: {e}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            ReducedDatumExtra.objects.create(target=dp.target, data_product=dp, data_type=dp.data_product_type,
                                             key='upload_extras', value=rd_extras)
            if dp.data_product_type == 'spectroscopy':
                # Date the spectrum by its observation, as sync_databases did
                DataProduct.objects.filter(pk=dp.pk).update(created=reduced_data.first().timestamp)
            if not settings.TARGET_PERMISSIONS_ONLY:
                set_dataproduct_view_groups(dp, groups)

        return Response({'id': dp.id, 'product_id': dp.product_id, 'target': dp.target_id,
                         'data': dp.data.name, 'reduceddatums': list(reduced_data.values_list('id', flat=True)),
                         'extras': rd_extras, 'groups': sorted(g.name for g in groups), 'replaced': existing is not None},
                        status=status.HTTP_201_CREATED)
