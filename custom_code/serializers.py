from django.conf import settings
from django.contrib.auth.models import Group
from guardian.shortcuts import assign_perm
from rest_framework import serializers
from tom_common.serializers import GroupSerializer
from tom_dataproducts.serializers import ReducedDatumSerializer
from tom_targets.serializers import TargetSerializer
from tom_targets.models import Target


class SNExReducedDatumSerializer(ReducedDatumSerializer):
    groups = GroupSerializer(many=True, required=False, write_only=True)

    class Meta(ReducedDatumSerializer.Meta):
        fields = ReducedDatumSerializer.Meta.fields + ('groups',)

    def validate_groups(self, groups):
        found = []
        for group in groups:
            lookup = {'pk': group['id']} if group.get('id') else {'name': group.get('name')}
            try:
                found.append(Group.objects.get(**lookup))
            except Group.DoesNotExist:
                raise serializers.ValidationError(f'Group {group} does not exist.')
        return found

    def create(self, validated_data):
        groups = validated_data.pop('groups', [])
        rd = super().create(validated_data)
        if not settings.TARGET_PERMISSIONS_ONLY:
            for group in groups:
                assign_perm('tom_dataproducts.view_reduceddatum', group, rd)
        return rd


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
