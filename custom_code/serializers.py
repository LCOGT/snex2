from django.conf import settings
from guardian.shortcuts import assign_perm
from rest_framework import serializers
from tom_common.serializers import GroupSerializer
from tom_dataproducts.serializers import DataProductSerializer, ReducedDatumSerializer
from tom_targets.fields import TargetFilteredPrimaryKeyRelatedField
from tom_targets.permissions import targets_for_user
from tom_targets.serializers import TargetSerializer
from tom_targets.models import Target

from custom_code.utils import groups_from_payload


class SNExTargetField(TargetFilteredPrimaryKeyRelatedField):
    def get_queryset(self):
        return targets_for_user(self.context['request'].user, Target.objects.all(), 'change_target')


class SNExDataProductSerializer(DataProductSerializer):
    target = SNExTargetField(queryset=Target.objects.all())


class SNExReducedDatumSerializer(ReducedDatumSerializer):
    target = SNExTargetField(queryset=Target.objects.all())
    groups = GroupSerializer(many=True, required=False, write_only=True)

    class Meta(ReducedDatumSerializer.Meta):
        fields = ('id',) + ReducedDatumSerializer.Meta.fields + ('groups',)

    def validate_groups(self, groups):
        groups = groups_from_payload(groups)
        user = self.context['request'].user
        if user.is_superuser or not groups:
            return groups
        member_of = [group for group in groups if group in user.groups.all()]
        if not member_of:
            raise serializers.ValidationError('You are not a member of any of these groups.')
        return member_of

    def _grant_view(self, rd, groups):
        if not settings.TARGET_PERMISSIONS_ONLY:
            for group in groups:
                assign_perm('tom_dataproducts.view_reduceddatum', group, rd)
        return rd

    def _stamp_uploader(self, validated_data):
        if isinstance(validated_data.get('value'), dict):
            validated_data['value']['uploaded_by'] = self.context['request'].user.username

    def create(self, validated_data):
        groups = validated_data.pop('groups', [])
        self._stamp_uploader(validated_data)
        return self._grant_view(super().create(validated_data), groups)

    def update(self, instance, validated_data):
        groups = validated_data.pop('groups', [])
        self._stamp_uploader(validated_data)
        if self.partial and 'value' in validated_data:
            validated_data['value'] = {**instance.value, **validated_data['value']}
        return self._grant_view(super().update(instance, validated_data), groups)


class SNExTargetSerializer(TargetSerializer):
    class Meta(TargetSerializer.Meta):
        extra_kwargs = {'name': {'validators': []}}

    def validate_groups(self, groups):
        groups_from_payload(groups)
        return groups

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
            standard=data.get('standard', inst.standard if inst else False),
        )
        if self.duplicate:
            target, matched_by = self.duplicate
            raise serializers.ValidationError(
                {'duplicate': {'id': target.id, 'name': target.name, 'matched_by': matched_by}}
            )
        return data
