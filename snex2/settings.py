"""
Django settings for your TOM project.

Originally generated by 'django-admin startproject' using Django 2.1.1.
Generated by ./manage.py tom_setup on Jan. 9, 2019, 10:20 p.m.

For more information on this file, see
https://docs.djangoproject.com/en/2.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/2.1/ref/settings/
"""

import os
import logging.config
import tempfile

from lcogt_logging import LCOGTFormatter

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/2.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'ks#e!w3m*y1g_=)%vmrdcyn*5dt0$)o^mq2f=vtj#myw#&amp;p3%i'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

ALLOWED_HOSTS = ['*']


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'django_extensions',
    'guardian',
    'tom_common',
    'django_comments',
    'bootstrap4',
    'crispy_forms',
    'django_filters',
    'django_gravatar',
    'tom_targets',
    'tom_alerts',
    'tom_catalogs',
    'tom_observations',
    'tom_dataproducts',
    'custom_code',
    'rest_framework',
    'rest_framework.authtoken',
    'django_plotly_dash.apps.DjangoPlotlyDashConfig',

]

SITE_ID = 2

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'tom_common.middleware.Raise403Middleware',
    'tom_common.middleware.ExternalServiceMiddleware',
    'tom_common.middleware.AuthStrategyMiddleware',
]

ROOT_URLCONF = 'snex2.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

CRISPY_TEMPLATE_PACK = 'bootstrap4'

WSGI_APPLICATION = 'snex2.wsgi.application'


# Database
# https://docs.djangoproject.com/en/2.1/ref/settings/#databases
if os.environ.get('SNEX2_DB_BACKEND') == 'postgres':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'snex2',
            'USER': os.environ['SNEX2_DB_USER'],
            'PASSWORD': os.environ['SNEX2_DB_PASSWORD'],
            'HOST': 'snex2-db',
            'PORT': 5432,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
        }
    }


# Password validation
# https://docs.djangoproject.com/en/2.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

AUTHENTICATION_BACKENDS = (
    'django.contrib.auth.backends.ModelBackend',
    'guardian.backends.ObjectPermissionBackend',
)

# Internationalization
# https://docs.djangoproject.com/en/2.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = False

USE_TZ = True

DATETIME_FORMAT = 'Y-m-d H:m:s'
DATE_FORMAT = 'Y-m-d'


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/2.1/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, '_static')
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]
MEDIA_ROOT = os.path.join(BASE_DIR, 'data')
MEDIA_URL = '/data/'

# Using AWS

DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
STATICFILES_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'

AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID', '')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRECT_ACCESS_KEY', '')
AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME', '')
AWS_S3_REGION_NAME = os.getenv('AWS_S3_REGION_NAME', '')
AWS_DEFAULT_ACL = None

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            '()': LCOGTFormatter
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'default'
        }
    },
    'loggers': {
        '': {
            'handlers': ['console'],
            'level': 'INFO'
        }
    }
}

# TOM Specific configuration
TARGET_TYPE = 'SIDEREAL'

FACILITIES = {
    'LCO': {
        'portal_url': 'https://observe.lco.global',
        'api_key': os.environ['LCO_APIKEY'],
    },
    'GEM': {
        'portal_url': {
            'GS': 'https://139.229.34.15:8443',
            'GN': 'https://128.171.88.221:8443',
        },
        'api_key': {
            'GS': '',
            'GN': '',
        },
        'user_email': '',
        'programs': {
            'GS-YYYYS-T-NNN': {
                'MM': 'Std: Some descriptive text',
                'NN': 'Rap: Some descriptive text'
            },
            'GN-YYYYS-T-NNN': {
                'QQ': 'Std: Some descriptive text',
                'PP': 'Rap: Some descriptive text',
            },
        },
    },
    'SNExGemini': {
        'portal_url': {
            'GS': 'https://139.229.34.15:8443',
            'GN': 'https://139.229.34.15:8443',
        },
        'api_key': {
            'GS': '',
            'GN': '',
        },
        'user_email': '',
        'programs': 'GS-2019A-Q-113' 
    }
}

# Define extra target fields here. Types can be any of "number", "st    ring", "boolean" or "datetime"
# See https://tomtoolkit.github.io/docs/target_fields for documentat    ion on this feature
# For example:
# EXTRA_FIELDS = [
#     {'name': 'redshift', 'type': 'number'},
#     {'name': 'discoverer', 'type': 'string'}
#     {'name': 'eligible', 'type': 'boolean'},
#     {'name': 'dicovery_date', 'type': 'datetime'}
# ]
EXTRA_FIELDS = [
    {'name': 'redshift', 'type': 'number'},
    {'name': 'classification', 'type': 'string'},
    {'name': 'tweet', 'type': 'boolean'},
]

# Authentication strategy can either be LOCKED (required login for all views)
# or READ_ONLY (read only access to views)
AUTH_STRATEGY = 'LOCKED'
#AUTH_STRATEGY = 'READ_ONLY'

TARGET_PERMISSIONS_ONLY = False

# URLs that should be allowed access even with AUTH_STRATEGY = LOCKED
# for example: OPEN_URLS = ['/', '/about']
OPEN_URLS = ['/snex2/tnstargets/', '/pipeline-upload/photometry-upload/']

HOOKS = {
    'target_post_save': 'custom_code.hooks.target_post_save',
    'observation_change_state': 'tom_common.hooks.observation_change_state',
    'targetextra_post_save': 'custom_code.hooks.targetextra_post_save'
}

TOM_ALERT_CLASSES = [
    'custom_code.brokers.mars.CustomMARSBroker',
    'tom_alerts.brokers.lasair.LasairBroker',
    ]

TOM_FACILITY_CLASSES = [
    #'tom_observations.facilities.gemini.GEMFacility',
    'custom_code.facilities.gemini_facility.GeminiFacility',
    #'tom_observations.facilities.lco.LCOFacility',
    'custom_code.facilities.lco_facility.SnexLCOFacility',
    #'custom_code.facilities.soar_facility.SOARFacility',
    'tom_observations.facilities.soar.SOARFacility',
    ]

TOM_HARVESTER_CLASSES = [
    'custom_code.harvesters.tns_harvester.TNSHarvester',
    'custom_code.harvesters.mars_harvester.MARSHarvester',
    'tom_catalogs.harvesters.simbad.SimbadHarvester',
    'tom_catalogs.harvesters.ned.NEDHarvester',
    #'tom_catalogs.harvesters.jplhorizons.JPLHorizonsHarvester',
    #'tom_catalogs.harvesters.mpc.MPCHarvester',
    ]

DATA_TYPES = (
    ('SPECTROSCOPY', 'Spectroscopy'),
    ('PHOTOMETRY', 'Photometry')
)

DATA_PRODUCT_TYPES = {
    'photometry': ('photometry', 'Photometry'),
    #'fits_file': ('fits_file', 'FITS File'),
    'spectroscopy': ('spectroscopy', 'Spectroscopy'),
    #'image_file': ('image_file', 'Image File')
}

DATA_PROCESSORS = {
    'photometry': 'custom_code.processors.photometry_processor.PhotometryProcessor',
    'spectroscopy': 'custom_code.processors.spectroscopy_processor.SpecProcessor',
    'fits_file': 'custom_code.processors.spectroscopy_processor.SpecProcessor',
}

#TOM_LATEX_PROCESSORS = {
#    'ObservationGroup': 'tom_publications.processors.observation_group_latex_processor.ObservationGroupLatexProcessor',
#    'TargetList': 'tom_publications.processors.target_list_latex_processor.TargetListLatexProcessor'
#}

REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication'
    ],
    'TEST_REQUEST_DEFAULT_FORMAT': 'json',
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.LimitOffsetPagination',
    'PAGE_SIZE': 100
}

TARGET_CLASSIFICATIONS = [
    'Afterglow', 'Afterglow?', 'AGN', 'AGN?', 'Ca-rich', 'Ca-rich?', 'CV', 'CV?', 'Galaxy', 'ILRN', 'ILRN?', 'Junk', 'Kilonova', 'Kilonova?', 'LBV', 'LBV?', 'Nova', 'Nova?', 'SLSN-I', 'SLSN-I?', 'SLSN-II', 'SLSN-II?', 'SLSN-R', 'SLSN-R?', 'SN', 'SN I-faint', 'SN I-faint?', 'SN Ia', 'SN Ia 02cx-like', 'SN Ia 02cx-like?', 'SN Ia 02es-like', 'SN Ia 02es-like?', 'SN Ia 02ic-like', 'SN Ia 02ic-like?', 'SN Ia 91bg-like', 'SN Ia 91bg-like?', 'SN Ia 91T-like', 'SN Ia 91T-like?', 'SN Ia pec', 'SN Ia pec?', 'SN Ia?', 'SN Ib', 'SN Ib/c', 'SN Ib/c?', 'SN Ib?', 'SN Ibn', 'SN Ibn?', 'SN Ic', 'SN Ic-BL', 'SN Ic-BL?', 'SN Ic?', 'SN Icn', 'SN II', 'SN II?', 'SN IIb', 'SN IIb?', 'SN IIL', 'SN IIL?', 'SN IIn', 'SN IIn?', 'SN IIP', 'SN IIP?', 'SN?', 'Standard', 'TDE', 'TDE?', 'Unknown', 'Varstar', 'Varstar?'
]

DEFAULT_GROUPS = [
    'ANU', 'ARIES', 'CSP', 'CU Boulder', 'e/PESSTO', 'ex-LCOGT', 'KMTNet', 'LBNL', 'LCOGT', 'LSQ', 'NAOC', 'Padova', 'QUB', 'SAAO', 'SIRAH', 'Skymapper', 'Tel Aviv U', 'U Penn', 'UC Berkeley', 'US GSP', 'UT Austin'
]

X_FRAME_OPTIONS = 'ALLOWALL'

HINTS_ENABLED = False
HINT_LEVEL = 20

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
    'django.contrib.auth.hashers.CryptPasswordHasher',
    #'django.contrib.auth.hashers.Argon2PasswordHasher',
    #'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
]

try:
    from local_settings import * # noqa
except ImportError:
    pass
