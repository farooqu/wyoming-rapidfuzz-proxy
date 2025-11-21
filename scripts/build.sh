#!/bin/bash

# Define the path to the VERSION file, assuming the script is executed from the project root.
VERSION_FILE="./wyoming_rapidfuzz_proxy/VERSION"

# Read version and clean up whitespace
WYOMING_RAPIDFUZZ_PROXY_VERSION=$(cat ${VERSION_FILE} | tr -d '\n' | tr -d ' ')

# Define the BUILD_FROM argument
BUILD_FROM=ubuntu:25.10

# --- Logic to detect the --enable-no-gil argument ---
ENABLE_NO_GIL="false"

# Loop through all arguments passed to the script
for arg in "$@"
do
    if [ "$arg" == "--enable-no-gil" ]; then
        ENABLE_NO_GIL="true"
        echo "[SCRIPT] NO-GIL Mode activated. Python will be compiled from source (Slow)."
    fi
done

if [ "$ENABLE_NO_GIL" == "false" ]; then
    echo "[SCRIPT] Standard Mode activated. Using system Python packages (Fast)."
fi
# ---------------------------------------------------------

# Execute docker build with the conditional argument
docker build \
     --build-arg BUILD_FROM=${BUILD_FROM} \
     --build-arg WYOMING_RAPIDFUZZ_PROXY=${WYOMING_RAPIDFUZZ_PROXY_VERSION} \
     --build-arg ENABLE_NO_GIL=${ENABLE_NO_GIL} \
     -t wyoming-rapidfuzz-proxy:${WYOMING_RAPIDFUZZ_PROXY_VERSION} \
     -t wyoming-rapidfuzz-proxy:latest \
     -f Dockerfile .
