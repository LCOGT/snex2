from django.conf import settings
from guardian.shortcuts import assign_perm
from rest_framework import serializers
from tom_common.serializers import GroupSerializer
from tom_dataproducts.serializers import ReducedDatumSerializer
from tom_targets.serializers import TargetSerializer
from tom_targets.models import Target

from custom_code.utils import groups_from_payload


class SNExReducedDatumSerializer(ReducedDatumSerializer):
    groups = GroupSerializer(many=True, required=False, write_only=True)

    class Meta(ReducedDatumSerializer.Meta):
        fields = ('id',) + ReducedDatumSerializer.Meta.fields + ('groups',)

    def validate_groups(self, groups):
        return groups_from_payload(groups)

    def _grant_view(self, rd, groups):
        if not settings.TARGET_PERMISSIONS_ONLY:
            for group in groups:
                assign_perm('tom_dataproducts.view_reduceddatum', group, rd)
        return rd

    def create(self, validated_data):
        groups = validated_data.pop('groups', [])
        return self._grant_view(super().create(validated_data), groups)

    def update(self, instance, validated_data):
        groups = validated_data.pop('groups', [])
        if self.partial and 'value' in validated_data:
            validated_data['value'] = {**instance.value, **validated_data['value']}
        return self._grant_view(super().update(instance, validated_data), groups)


class SNExTargetSerializer(TargetSerializer):
    class Meta(TargetSerializer.Meta):
        extra_kwargs = {'name': {'validators': []}}

    def update(self, instance, validated_data):
        for field in instance._meta.local_concrete_fields:
            if not field.primary_key and field.name in validated_data:
                setattr(instance, field.name, validated_data[field.name])
        return super().update(instance, validated_data)

    def validate(self, data):

        data = super().validate(data)
        inst = self.instance
        self.duplicate = Target.matches.find_duplicate(
            data.get('name', inst.name if inst else ''),
            data.get('ra', inst.ra if inst else None),
            data.get('dec', inst.dec if inst else None),
            exclude_pk=inst.pk if inst else None,
        )
        if self.duplicate:
            target, matched_by = self.duplicate
            raise serializers.ValidationError(
                {'duplicate': {'id': target.id, 'name': target.name, 'matched_by': matched_by}}
            )
        return data
