#!/bin/sh -e

# if FLASK_DEBUG is defined, we'll run via flask with dynamic reloading of
# code changes to disk. This is helpful for debugging something already in k8s

if [ -z "$FLASK_DEBUG" ] ; then
	exec /usr/bin/gunicorn -w4 -b 0.0.0.0:8000 $FLASK_APP
fi

exec /usr/bin/flask run -h 0.0.0.0 -p 8000