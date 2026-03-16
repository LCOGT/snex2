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

def convert_field_to_date(chunk_size=1000):
    qs = ObservationRecord.objects.only("id", "parameters").iterator(chunk_size=chunk_size)
    updated_count = 0
    for obs in qs:
        params = obs.parameters or {}
        updated = False
        cad_freq_days = params.get("cadence_frequency_days")
        if not cad_freq_days:
            cad_freq = params.get("cadence_frequency")
            if cad_freq:
                params["cadence_frequency_days"] = cad_freq
                updated = True
        reminder_date = params.get("reminder_date")
        if not reminder_date:
            reminder = params.get("reminder")
            if reminder:
                params["reminder_date"] = reminder
                updated = True
        if updated:
            obs.parameters = params
            obs.save(update_fields=["parameters"])
            updated_count += 1
    print(f"Updated {updated_count} records")
