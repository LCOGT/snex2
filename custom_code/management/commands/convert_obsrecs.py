from tom_observations.models import ObservationRecord
from django.contrib.auth.models import User

#only should need to run once to convert the obsrecord start_user to username
def convert_obs_records():
    obsrecs = ObservationRecord.objects.all()
    for obs in obsrecs:
        start_user = obs.parameters.get('start_user')
        if start_user:
            user = User.objects.filter(first_name = start_user).first()
            obs.parameters['start_user'] = user.username
            print(start_user, user, user.username)
            obs.save()
