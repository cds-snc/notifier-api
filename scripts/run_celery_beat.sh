#!/bin/sh

set -e

poetry run celery -A run_celery.notify_celery beat --loglevel=INFO
