# Base: Debian Trixie with Python 3.13
FROM python:3.13-slim-trixie AS rdfc-base

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    wget \
    unzip \
    git \
    openjdk-21-jdk-headless \
    nodejs \
    npm \
    make \
    g++ \
    libc-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Gradle
ARG GRADLE_VERSION=8.10.2
RUN wget https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip -P /tmp \
    && unzip /tmp/gradle-${GRADLE_VERSION}-bin.zip -d /opt \
    && ln -s /opt/gradle-${GRADLE_VERSION}/bin/gradle /usr/bin/gradle \
    && rm -rf /tmp/*.zip

# Install Python tools
RUN pip install --no-cache-dir hatch uv

# Set working directory
WORKDIR /workspace/pipeline

# Copy dependency files
# package.json: Lists javascript dependencies that can be installed via npm
# pyproject.toml: Lists python dependencies that can be installed via pip. Is either fetched from PyPl or from the local_packages folder
# local_packages: In case packages are not public on PyPl, place them in the local_packages-folder and list the dependency explicitly in pyproject.toml (see example in file) 
COPY package.json package-lock.json* ./ 
COPY pyproject.toml ./ 
COPY local_processors ./local_processors 

# Creating virtual environment for the python runner
# (this is already defined in the pyproject.toml file I think)
# RUN python -m venv .venv
# ENV PATH="/workspace/pipeline/.venv/bin:$PATH"

# Install dependencies
# First line: Installs all dependencies listed in pyproject.toml (including those in local_packages, if they are explicitly listed in pyproject.toml)
# Second line: Installs all dependencies listed in package.json
RUN pip install --no-cache-dir .
RUN npm install

# Keep container interactive
CMD ["/bin/bash"]