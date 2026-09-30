from rest_framework import serializers
from tom_targets.serializers import TargetSerializer
from tom_targets.models import Target


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
