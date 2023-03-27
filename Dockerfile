FROM python:3.10-alpine3.16@sha256:923d30cb18f3d67160cb055a3c3862771edc5d07c63d54d9ca144954c3a6bdfe

ENV PYTHONPATH "${PYTHONPATH}:/opt/python/lib/python3.10/site-packages"
ENV PYTHONDONTWRITEBYTECODE 1
ENV TASK_ROOT /app
ENV APP_VENV="${TASK_ROOT}/.venv"
ENV POETRY_HOME="/opt/poetry"
ENV POETRY_VERSION="1.3.2"
ENV POETRY_VIRTUALENVS_CREATE="false"
ENV PATH="${APP_VENV}/bin:${POETRY_HOME}/bin:$PATH"

RUN apk add --no-cache bash build-base git libtool cmake autoconf automake gcc musl-dev postgresql-dev g++ libc6-compat libexecinfo-dev make libffi-dev libmagic libcurl curl-dev rust cargo && rm -rf /var/cache/apk/*

RUN mkdir -p ${TASK_ROOT}
WORKDIR ${TASK_ROOT}

# Install poetry and isolate it in it's own venv
RUN python -m venv ${POETRY_HOME} \
    && ${POETRY_HOME}/bin/pip3 install poetry==${POETRY_VERSION}

COPY pyproject.toml poetry.lock ${TASK_ROOT}/

RUN python -m venv ${APP_VENV} \
    && . ${APP_VENV}/bin/activate \
    && poetry install \
    && poetry add awslambdaric newrelic-lambda wheel

COPY . ${TASK_ROOT}/

RUN . ${APP_VENV}/bin/activate \
    && make generate-version-file

ENV PORT=6011

ARG GIT_SHA
ENV GIT_SHA ${GIT_SHA}

# (Optional) Add Lambda Runtime Interface Emulator and use a script in the ENTRYPOINT for simpler local runs
ADD https://github.com/aws/aws-lambda-runtime-interface-emulator/releases/latest/download/aws-lambda-rie /usr/bin/aws-lambda-rie
COPY bin/entry.sh /
COPY bin/sync_lambda_envs.sh /
RUN chmod 755 /usr/bin/aws-lambda-rie /entry.sh /sync_lambda_envs.sh

# New Relic lambda layer
RUN unzip newrelic-layer.zip -d /opt && rm newrelic-layer.zip

ENTRYPOINT [ "/entry.sh" ]

# Launch the New Relic lambda wrapper which will then launch the app
# handler defined in the NEW_RELIC_LAMBDA_HANDLER environment variable
CMD [ "newrelic_lambda_wrapper.handler" ]