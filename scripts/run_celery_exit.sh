#!/bin/bash

set -e

TERMINATE_TIMEOUT=9

function get_celery_pids {
  # get the PIDs of the process whose parent is the root process
  # print only pid and their command, get the ones with "celery" in their name
  # and keep only these PIDs

  set +o pipefail # so grep returning no matches does not premature fail pipe
  # shellcheck disable=SC2009 # We don't want to bother re-writing this to use pgrep
  APP_PIDS=$(ps aux --sort=start_time | grep 'celery worker' | grep 'bin/celery' | head -1 | awk '{print $2}')
  set -o pipefail # pipefail should be set everywhere else
}

function send_signal_to_celery_processes {
  # refresh pids to account for the case that some workers may have terminated but others not
  get_celery_pids
  # send signal to all remaining apps
  echo "${APP_PIDS}" | tr -d '\n' | tr -s ' ' | xargs echo "Sending signal ${1} to processes with pids: " >> /proc/1/fd/1
  echo "We will send ${1} signal" >> /proc/1/fd/1
  for value in ${APP_PIDS}
  do
    echo kill -s "${1}" "$value"
    kill -s "${1}" "$value"
  done
  #echo ${APP_PIDS} | xargs kill -s ${1}
}

function error_exit()
{
    echo "Error: $1" >> /proc/1/fd/1
}

function ensure_celery_is_running {
  if [ "${APP_PIDS}" = "" ]; then
    echo "There are no celery processes running, this container is bad" >> /proc/1/fd/1

    echo "Exporting CF information for diagnosis" >> /proc/1/fd/1

    env | grep CF

    exit 1
  fi
}


function on_exit {
  apk add --no-cache procps
  apk add --no-cache coreutils
  echo "multi worker app exiting" >> /proc/1/fd/1
  wait_time=0

  send_signal_to_celery_processes TERM

  # check if the apps are still running every second
  while [[ "$wait_time" -le "$TERMINATE_TIMEOUT" ]]; do
    echo "exit function is running with wait time of 9s" >> /proc/1/fd/1
    get_celery_pids
    ensure_celery_is_running
    # shellcheck disable=SC2219 # We could probably rewrite it as `((wait_time++)) || true` but I haven't tested and I assume this works as is
    let wait_time=wait_time+1
    sleep 1
  done

  echo "sending signal to celery to kill process as TERM signal has not timed out" >> /proc/1/fd/1
  send_signal_to_celery_processes KILL
}

echo "Run script pid: $$" >> /proc/1/fd/1

on_exit
