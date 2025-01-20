FROM mcr.microsoft.com/vscode/devcontainers/python:3.13

ARG KUBENS_VERSION="0.9.4"
ENV POETRY_VERSION="1.7.1"

# Install packages
RUN apt-get update \
    && apt-get -y install --no-install-recommends \
    apt-utils \
    postgresql-client \
    2>&1 \
    && apt-get -y install \
    curl \
    dbus-x11 \
    dnsutils \
    emacs \
    exa \
    fd-find \
    fzf \
    git \
    graphviz \
    iproute2 \
    iputils-ping \
    kcachegrind \
    less \
    libsodium-dev \
    lsb-release \
    man-db \
    manpages \
    net-tools \
    nodejs \
    npm \
    openssh-client \
    procps \
    pyprof2calltree \
    ripgrep \
    sudo \
    tldr \
    unzip \
    vim \
    libgtk2.0-0 \
    libgtk-3-0 \
    libgbm-dev \
    libnotify-dev \
    libgconf-2-4 \
    libnss3 \
    libxss1 \
    libasound2 \
    libxtst6 \
    xauth \
    xvfb \
    && apt-get autoremove -y \
    && apt-get clean -y \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN pip install --upgrade pip

# Install kubens
RUN git clone -b v${KUBENS_VERSION} --single-branch https://github.com/ahmetb/kubectx /opt/kubectx \
    && ln -s /opt/kubectx/kubectx /usr/local/bin/kubectx \
    && ln -s /opt/kubectx/kubens /usr/local/bin/kubens

COPY .devcontainer/scripts/notify-dev-entrypoint.sh /usr/local/bin/

ENV SHELL /bin/zsh

EXPOSE 8000
EXPOSE 6011
