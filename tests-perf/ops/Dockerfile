FROM python:3.10-alpine3.16@sha256:923d30cb18f3d67160cb055a3c3862771edc5d07c63d54d9ca144954c3a6bdfe

ENV PYTHONDONTWRITEBYTECODE 1

RUN apk add --no-cache bash build-base git libtool cmake autoconf automake gcc musl-dev postgresql-dev g++ libexecinfo-dev make libffi-dev libmagic libcurl curl-dev rust cargo && rm -rf /var/cache/apk/*

# update pip
RUN python -m pip install wheel
RUN python -m pip install --upgrade pip

RUN set -ex && mkdir /app

WORKDIR /app

COPY . /app

RUN set -ex && pip3 install -r requirements_for_test.txt
RUN echo "fs.file-max = 100000" >> /etc/sysctl.conf

ENTRYPOINT [ "bin/execute_and_publish_performance_test.sh" ]
