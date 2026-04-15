from rest_framework import serializers
from tom_targets.serializers import TargetSerializer
from tom_targets.models import Target

class SNExTargetSerializer(TargetSerializer):
    def validate(self, data):
        data = super().validate(data)
        ra = data.get('ra')
        dec = data.get('dec')
        if ra is not None and dec is not None:
            nearby = Target.objects.filter(
                ra__gte=ra - 4/3600,
                ra__lte=ra + 4/3600,
                dec__gte=dec - 4/3600,
                dec__lte=dec + 4/3600
            )
            if self.instance:
                nearby = nearby.exclude(pk=self.instance.pk)
            if nearby.exists():
                raise serializers.ValidationError('Target exists near these coordinates.')
        return data