#!/bin/bash

# Define the path to the VERSION file, assuming the script is executed from the project root.
VERSION_FILE="./wyoming_rapidfuzz_proxy/VERSION"

# Read the content of the VERSION file and assign it to the variable.
# This will strip newlines and whitespace, ensuring only the version number is captured.
WYOMING_RAPIDFUZZ_PROXY_VERSION=$(cat ${VERSION_FILE} | tr -d '\n' | tr -d ' ')

# Define the BUILD_FROM argument
BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm

# Execute the docker build command using the variable.
docker build --build-arg BUILD_FROM=${BUILD_FROM} \
     --build-arg WYOMING_RAPIDFUZZ_PROXY=${WYOMING_RAPIDFUZZ_PROXY_VERSION} \
     -t wyoming-rapidfuzz-proxy:${WYOMING_RAPIDFUZZ_PROXY_VERSION} \
     -t wyoming-rapidfuzz-proxy:latest \
     -f Dockerfile .
