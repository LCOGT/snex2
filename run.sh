#!/bin/bash
python manage.py migrate --noinput
python manage.py createcachetable
gunicorn -b 0.0.0.0:8080 snex2.wsgi --access-logfile - --error-logfile - -k gevent --timeout 300 --workers 12