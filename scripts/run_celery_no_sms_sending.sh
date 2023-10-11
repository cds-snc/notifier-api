#!/bin/sh

# runs celery with only the throttled sms sending queue

init()
{
     # Wait for cwagent to become available.   
    while :
    do
        if  nc -vz $STATSD_HOST 25888; then
            echo "CWAgent is Ready."
            break;
        else
            echo "Waiting for CWAgent to become ready."
            sleep 1
        fi
    done
}

# runs celery with all celery queues except send-throttled-sms-tasks, send-sms-tasks, send-sms-high, send-sms-medium, or send-sms-low

set -e

# Check and see if this is running in K8s and if so, wait for cloudwatch agent
if [[ -z "${STATSD_HOST}" ]]; then
    init
fi

echo "Start celery, concurrency: ${CELERY_CONCURRENCY-4}"

celery -A run_celery.notify_celery worker --pidfile="/tmp/celery.pid" --loglevel=INFO --concurrency=${CELERY_CONCURRENCY-4} -Q database-tasks,-priority-database-tasks.fifo,-normal-database-tasks,-bulk-database-tasks,job-tasks,notify-internal-tasks,periodic-tasks,priority-tasks,normal-tasks,bulk-tasks,reporting-tasks,research-mode-tasks,retry-tasks,send-email-tasks,service-callbacks,delivery-receipts
