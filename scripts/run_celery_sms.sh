#!/bin/sh

set -e

poetry run celery -A run_celery.notify_celery worker --loglevel=INFO --concurrency=1 -Q send-throttled-sms-tasks
