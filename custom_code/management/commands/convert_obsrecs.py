from tom_observations.models import ObservationRecord
from django.contrib.auth.models import User
import re

def contains_capital_regex(s):
    return bool(re.search(r'[A-Z]', s))

#only should need to run once to convert the obsrecord start_user to username
def convert_start_user():
    qs = ObservationRecord.objects.only('id', 'parameters').filter(
        parameters__has_key='start_user'
    )

    user_map = dict(
        User.objects.exclude(first_name='')
        .values_list('first_name', 'username')
    )

    batch = []
    BATCH_SIZE = 500

    for o in qs.iterator(chunk_size=500):
        start_user = o.parameters.get('start_user')
        if start_user and start_user.istitle() and start_user in user_map:
            o.parameters['start_user'] = user_map[start_user]
            batch.append(o)
        if len(batch) >= BATCH_SIZE:
            ObservationRecord.objects.bulk_update(batch, ['parameters'])
            batch.clear()

    if batch:
        ObservationRecord.objects.bulk_update(batch, ['parameters'])


def convert_field_to_date():
    qs = ObservationRecord.objects.only("id", "parameters")
    batch = []
    BATCH_SIZE = 500

    for obs in qs.iterator(chunk_size=500):
        params = obs.parameters or {}
        updated = False
        cad_freq_days = params.get("cadence_frequency_days")
        if not cad_freq_days:
            cad_freq = params.get("cadence_frequency")
            if cad_freq:
                params["cadence_frequency_days"] = cad_freq
                updated = True
            if cad_freq == 0:
                params["cadence_frequency_days"] = cad_freq
                updated = True
        reminder_date = params.get("reminder_date")
        if not reminder_date:
            reminder = params.get("reminder")
            if reminder:
                params["reminder_date"] = reminder
                updated = True
            if reminder == 0:
                params["reminder_date"] = reminder
                updated = True
        if updated:
            obs.parameters = params
            batch.append(obs)
        if len(batch) >= BATCH_SIZE:
            ObservationRecord.objects.bulk_update(batch, ["parameters"])
            batch.clear()

    if batch:
        ObservationRecord.objects.bulk_update(batch, ["parameters"])
