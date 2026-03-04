# Generated manually for SNEx2 registration "who you are" field

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('custom_code', '0014_alter_snextarget_redshift'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserRegistrationInfo',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('who_you_are', models.TextField(help_text='Please briefly describe who you are or which group/institution you work with.', verbose_name='Who you are / who you are working with')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='registration_info', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
