ARG BUILD_FROM=ubuntu:25.10
FROM ${BUILD_FROM}

ARG PYTHON_VERSION=3.14.0
ARG ENABLE_NO_GIL=false

ENV PIP_BREAK_SYSTEM_PACKAGES=1
ENV DEBIAN_FRONTEND=noninteractive

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /usr/wyoming_rapidfuzz_proxy

RUN apt-get update && \
    if [ "$ENABLE_NO_GIL" = "true" ]; then \
        echo ">>> NO-GIL MODE SELECTED: Compiling from source (Slower)..." && \
        apt-get install -y --no-install-recommends \
            build-essential wget libssl-dev zlib1g-dev libbz2-dev \
            libreadline-dev libsqlite3-dev libncursesw5-dev xz-utils \
            tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
            ca-certificates && \
        wget https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz && \
        tar xvf Python-${PYTHON_VERSION}.tgz && \
        cd Python-${PYTHON_VERSION} && \
        ./configure --enable-optimizations --disable-gil --with-ensurepip=install && \
        make profile-opt -j$(nproc) && \
        make altinstall && \
        ln -sf /usr/local/bin/python3.14t /usr/local/bin/python3 && \
        ln -sf /usr/local/bin/python3.14t /usr/local/bin/python && \
        if [ -f /usr/local/bin/pip3.14t ]; then \
           ln -sf /usr/local/bin/pip3.14t /usr/local/bin/pip && \
           ln -sf /usr/local/bin/pip3.14t /usr/local/bin/pip3; \
        else \
           ln -sf /usr/local/bin/pip3.14 /usr/local/bin/pip && \
           ln -sf /usr/local/bin/pip3.14 /usr/local/bin/pip3; \
        fi && \
        cd .. && \
        rm -rf Python-${PYTHON_VERSION} Python-${PYTHON_VERSION}.tgz && \
        apt-get purge -y build-essential wget && \
        apt-get autoremove -y; \
    else \
        echo ">>> STANDARD MODE SELECTED: Using system packages (Fast)..." && \
        apt-get install -y --no-install-recommends \
            python3 \
            python3-pip \
            ca-certificates && \
        ln -sf /usr/bin/python3 /usr/bin/python; \
    fi && \
    rm -rf /var/lib/apt/lists/*

COPY wyoming_rapidfuzz_proxy ./wyoming_rapidfuzz_proxy
COPY scripts ./
COPY requirements.txt ./

RUN python3 -m pip install --no-cache-dir -r requirements.txt

RUN python3 --version && python3 -c "import sys; print(f'--> STATUS: GIL is {\"ENABLED\" if sys._is_gil_enabled() else \"DISABLED (NO-GIL ACTIVE)\"}')"

ENTRYPOINT ["bash", "start.sh"]
