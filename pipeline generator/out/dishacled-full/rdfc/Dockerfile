# Base: Alpine with Python 3.13
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

# Install Gradle 8.10.2
ARG GRADLE_VERSION=8.10.2
RUN wget https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip -P /tmp \
    && unzip /tmp/gradle-${GRADLE_VERSION}-bin.zip -d /opt \
    && ln -s /opt/gradle-${GRADLE_VERSION}/bin/gradle /usr/bin/gradle \
    && rm -rf /tmp/*.zip

# Install Python tools Hatch and uv
RUN pip install --no-cache-dir hatch uv

# Set working directory
WORKDIR /workspace

# Copy package.json and package-lock.json for npm caching
COPY package.json ./pipeline/

# Install all processors listed in package.json
RUN cd pipeline && npm install

# Set default working dir
WORKDIR /workspace/pipeline

# Keep container interactive
CMD ["/bin/bash"]