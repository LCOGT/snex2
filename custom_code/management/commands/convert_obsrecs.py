from tom_observations.models import ObservationRecord
from django.contrib.auth.models import User
import re

def contains_capital_regex(s):
    return bool(re.search(r'[A-Z]', s))

#only should need to run once to convert the obsrecord start_user to username
def convert_start_user():
    obsrecs = ObservationRecord.objects.all()
    for obs in obsrecs:
        start_user = obs.parameters.get('start_user')
        if start_user:
            if contains_capital_regex(start_user):
                user = User.objects.filter(first_name = start_user).first()
                obs.parameters['start_user'] = user.username
                print(start_user, user, user.username)
                obs.save()
            else:
                print(start_user)

def convert_field_to_date():
    obsrecs = ObservationRecord.objects.all()
    for obs in obsrecs:
        cad_freq_days = obs.parameters.get('cadence_frequency_days')
        if not cad_freq_days:
            cad_freq = obs.parameters.get('cadence_frequency')
            if cad_freq:
                print(cad_freq_days,cad_freq)
                obs.parameters['cadence_frequency_days'] = cad_freq
                obs.save()
        reminder_date = obs.parameters.get('reminder_date')
        if not reminder_date:
            reminder = obs.parameters.get('reminder')
            if reminder:
                obs.parameters['reminder_date'] = reminder
                obs.save()


