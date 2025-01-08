FROM python:3.12-alpine3.20@sha256:5049c050bdc68575a10bcb1885baa0689b6c15152d8a56a7e399fb49f783bf98

ENV PYTHONDONTWRITEBYTECODE 1
ENV POETRY_VERSION "1.7.1"
ENV APP_VENV="/app/.venv"
ENV POETRY_HOME="/opt/poetry"
ENV POETRY_VERSION="1.7.1"
ENV POETRY_VIRTUALENVS_CREATE="false"
ENV PATH="${APP_VENV}/bin:${POETRY_HOME}/bin:$PATH"

RUN apk add --no-cache bash build-base git libtool cmake autoconf automake gcc musl-dev postgresql-dev g++ elfutils-dev make libffi-dev libmagic libcurl curl-dev rust cargo && rm -rf /var/cache/apk/*

RUN set -ex && mkdir /app
WORKDIR /app

# Install poetry and isolate it in it's own venv
RUN python -m venv ${POETRY_HOME} \
    && ${POETRY_HOME}/bin/pip3 install poetry==${POETRY_VERSION}

COPY pyproject.toml poetry.lock /app/

RUN python -m venv ${APP_VENV} \
    && . ${APP_VENV}/bin/activate \
    && poetry install \
    && poetry add wheel

COPY . /app/

RUN . ${APP_VENV}/bin/activate \
    && make generate-version-file


RUN echo "fs.file-max = 100000" >> /etc/sysctl.conf

ENTRYPOINT [ "bin/execute_and_publish_performance_test.sh" ]
