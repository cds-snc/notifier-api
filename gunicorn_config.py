import os
import sys
import time
import traceback

import gunicorn  # type: ignore
import newrelic.agent  # See https://bit.ly/2xBVKBH

environment = os.environ.get("NOTIFY_ENVIRONMENT")
newrelic.agent.initialize(environment=environment)  # noqa: E402

workers = 4
worker_class = "gevent"
worker_connections = 256
bind = "0.0.0.0:{}".format(os.getenv("PORT"))
accesslog = "-"
# Guincorn sets the server type on our app. We don't want to show it in the header in the response.
gunicorn.SERVER = "Undisclosed"

on_aws = environment in [
    "production",
    "staging",
    "scratch",
    "dev",
]
if on_aws:
    # To avoid load balancers reporting errors on shutdown instances, see AWS doc
    # > We also recommend that you configure the idle timeout of your application
    # > to be larger than the idle timeout configured for the load balancer.
    # > By default, Elastic Load Balancing sets the idle timeout value for your load balancer to 60 seconds.
    # https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html#connection-idle-timeout
    keepalive = 75

    # The default graceful timeout period for Kubernetes is 30 seconds, so
    # make sure that the timeouts defined here are less than the configured
    # Kubernetes timeout. This ensures that the gunicorn worker will exit
    # before the Kubernetes pod is terminated. This is important because
    # Kubernetes will send a SIGKILL to the pod if it does not terminate
    # within the grace period. If the worker is still processing requests
    # when it receives the SIGKILL, it will be terminated abruptly and
    # will not be able to finish processing the request. This can lead to
    # 502 errors being returned to the client.
    #
    # Also, some libraries such as NewRelic might need some time to finish
    # initialization before the worker can start processing requests. The
    # timeout values should consider these factors.
    #
    # Gunicorn config:
    # https://docs.gunicorn.org/en/stable/settings.html#graceful-timeout
    #
    # Kubernetes config:
    # https://kubernetes.io/docs/concepts/containers/container-lifecycle-hooks/
    graceful_timeout = 85
    timeout = 90

# Start timer for total running time
start_time = time.time()


def on_starting(server):
    server.log.info("Starting Notifications API")


def worker_abort(worker):
    worker.log.info("worker received ABORT {}".format(worker.pid))
    for threadId, stack in sys._current_frames().items():
        worker.log.error("".join(traceback.format_stack(stack)))


def on_exit(server):
    elapsed_time = time.time() - start_time
    server.log.info("Stopping Notifications API")
    server.log.info("Total gunicorn API running time: {:.2f} seconds".format(elapsed_time))


def worker_int(worker):
    worker.log.info("worker: received SIGINT {}".format(worker.pid))
