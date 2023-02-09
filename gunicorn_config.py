import os
import sys
import traceback

import newrelic.agent  # See https://bit.ly/2xBVKBH

newrelic.agent.initialize()  # noqa: E402

workers = 4
worker_class = "gevent"
worker_connections = 256
bind = "0.0.0.0:{}".format(os.getenv("PORT"))
accesslog = "-"

on_aws = os.environ.get("NOTIFY_ENVIRONMENT", "") in ["production", "staging"]
if on_aws:
    # To avoid load balancers reporting errors on shutdown instances, see AWS doc
    # > We also recommend that you configure the idle timeout of your application
    # > to be larger than the idle timeout configured for the load balancer.
    # > By default, Elastic Load Balancing sets the idle timeout value for your load balancer to 60 seconds.
    # https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html#connection-idle-timeout
    keepalive = 75

    # The default graceful timeout period for Kubernetes is 30 seconds, so
    # want a lower graceful timeout value for gunicorn so that proper instance
    # shutdowns.
    #
    # Gunicorn config:
    # https://docs.gunicorn.org/en/stable/settings.html#graceful-timeout
    #
    # Kubernetes config:
    # https://kubernetes.io/docs/concepts/containers/container-lifecycle-hooks/
    graceful_timeout = 20


def on_starting(server):
    server.log.info("Starting Notifications API")


def worker_abort(worker):
    worker.log.info("worker received ABORT {}".format(worker.pid))
    for threadId, stack in sys._current_frames().items():
        worker.log.error("".join(traceback.format_stack(stack)))


def on_exit(server):
    server.log.info("Stopping Notifications API")


def worker_int(worker):
    worker.log.info("worker: received SIGINT {}".format(worker.pid))
