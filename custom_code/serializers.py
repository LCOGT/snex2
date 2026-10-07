from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from guardian.shortcuts import assign_perm, get_groups_with_perms
from rest_framework import serializers
from tom_common.serializers import GroupSerializer
from tom_dataproducts.models import PhotometryReducedDatum, try_parse_reduced_datum
from tom_dataproducts.serializers import DataProductSerializer, ReducedDatumSerializer
from tom_targets.fields import TargetFilteredPrimaryKeyRelatedField
from tom_targets.permissions import targets_for_user
from tom_targets.serializers import TargetSerializer
from tom_targets.models import Target

from custom_code.utils import default_target_groups, groups_from_payload, without_sentinels, photometry_reduction_version, view_datum_perm


class SNExTargetField(TargetFilteredPrimaryKeyRelatedField):
    def get_queryset(self):
        return targets_for_user(self.context['request'].user, Target.objects.all(), 'change_target')


class SNExDataProductSerializer(DataProductSerializer):
    target = SNExTargetField(queryset=Target.objects.all())


class GroupsFieldMixin(serializers.Serializer):
    groups = GroupSerializer(many=True, required=False, write_only=True)

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
                assign_perm(view_datum_perm(type(rd)), group, rd)
        return rd


class SNExPhotometrySerializer(GroupsFieldMixin, serializers.ModelSerializer):
    target = SNExTargetField(queryset=Target.objects.all())
    value = serializers.JSONField()

    class Meta:
        model = PhotometryReducedDatum
        columns = ('brightness', 'brightness_error', 'bandpass', 'limit', 'telescope', 'instrument')
        fields = ('id', 'target', 'timestamp', 'source_name', 'source_location') + columns + (
            'reduction_version', 'value', 'groups')
        read_only_fields = columns + ('reduction_version',)

    def validate_value(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('Expected a dictionary.')
        return without_sentinels(value)

    def create(self, validated_data):
        try:
            return self.update_or_create(validated_data)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)

    def update_or_create(self, validated_data):
        groups = validated_data.pop('groups', None)
        if groups is None:
            target = validated_data['target']
            groups = [] if target.standard else list(get_groups_with_perms(target))
        posted = validated_data['value']
        version = photometry_reduction_version(posted)
        datum = try_parse_reduced_datum({
            **validated_data, 'data_type': 'photometry', 'reduction_version': version,
            'value': {**posted, 'uploaded_by': self.context['request'].user.username}})
        existing = PhotometryReducedDatum.objects.filter(
            target=datum.target, value__basename=posted['basename'], reduction_version=version
        ).first() if posted.get('basename') else None
        if existing is None:
            datum.save()
            self.result = 'created'
            return self._grant_view(datum, groups)
        columns = self.Meta.columns
        if existing.value.get('final_reduction') and not posted.get('final_reduction'):
            self.result = 'kept_final'
        elif all(getattr(existing, column) == getattr(datum, column) for column in columns) and all(
                existing.value.get(key) == extra for key, extra in datum.value.items() if key != 'uploaded_by'):
            self.result = 'unchanged'
        else:
            for field in ('timestamp', 'source_name', 'source_location') + columns:
                setattr(existing, field, getattr(datum, field))
            existing.value = {**existing.value, **datum.value}
            existing.save()
            self.result = 'updated'
        return existing


class SNExReducedDatumSerializer(GroupsFieldMixin, ReducedDatumSerializer):
    target = SNExTargetField(queryset=Target.objects.all())

    class Meta(ReducedDatumSerializer.Meta):
        fields = ('id',) + ReducedDatumSerializer.Meta.fields + ('groups',)

    def _stamp_uploader(self, validated_data):
        if isinstance(validated_data.get('value'), dict):
            validated_data['value']['uploaded_by'] = self.context['request'].user.username

    def create(self, validated_data):
        groups = validated_data.pop('groups', [])
        self._stamp_uploader(validated_data)
        return self._grant_view(super().create(validated_data), groups)


class SNExTargetSerializer(TargetSerializer):
    class Meta(TargetSerializer.Meta):
        extra_kwargs = {'name': {'validators': []}}

    def validate_groups(self, groups):
        groups_from_payload(groups)
        return groups

    def create(self, validated_data):
        if 'groups' not in self.initial_data and not validated_data.get('standard'):
            validated_data['groups'] = [
                {'name': group.name} for group in default_target_groups(self.context['request'].user)]
        return super().create(validated_data)

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
