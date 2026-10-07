from django.db import migrations

RENAMES = {
    'SN Ia 02ic-like': 'SN Ia-CSM',
    'SN Ia 02cx-like': 'SN Iax[02cx-like]',
    'SN Ia 91T-like': 'SN Ia-91T-like',
    'SN Ia 91bg-like': 'SN Ia-91bg-like',
    'SN Ia pec': 'SN Ia-pec',
}


def rename(apps, renames):
    SNExTarget = apps.get_model('custom_code', 'SNExTarget')
    for old, new in renames.items():
        for suffix in ('', '?'):
            SNExTarget.objects.filter(classification=old + suffix).update(classification=new + suffix)


def forwards(apps, schema_editor):
    rename(apps, RENAMES)


def backwards(apps, schema_editor):
    rename(apps, {new: old for old, new in RENAMES.items()})


class Migration(migrations.Migration):

    dependencies = [
        ('custom_code', '0024_spectroscopy_unique_flux_hash'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
