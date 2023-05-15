#!/bin/sh

set -e

echo "Start celery"

poetry run celery -A run_celery.notify_celery worker --pidfile="/tmp/celery.pid" --loglevel=INFO --concurrency=4 -Q database-tasks,-priority-database-tasks.fifo,-normal-database-tasks,-bulk-database-tasks,job-tasks,notify-internal-tasks,periodic-tasks,priority-tasks,normal-tasks,bulk-tasks,reporting-tasks,research-mode-tasks,retry-tasks,send-sms-tasks,send-email-tasks,service-callbacks,delivery-receipts
