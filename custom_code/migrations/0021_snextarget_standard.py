from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('custom_code', '0020_alter_reduceddatumextra_data_product'),
    ]

    operations = [
        migrations.AddField(
            model_name='snextarget',
            name='standard',
            field=models.BooleanField(default=False),
        ),
    ]
