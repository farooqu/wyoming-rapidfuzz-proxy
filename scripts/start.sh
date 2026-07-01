#!/bin/bash

VERSION_FILE="./wyoming_rapidfuzz_proxy/VERSION"
APP_VERSION=$(cat ${VERSION_FILE} | tr -d '\n' | tr -d ' ')

echo --------------------------------------------
echo Container Application Name: wyoming-rapidfuzz-proxy
echo Container Application Version: ${APP_VERSION}
echo --------------------------------------------

flags=()

CORRECTION_THRESHOLD=${CORRECTION_THRESHOLD:-15}
REFRESH_INTERVAL=${REFRESH_INTERVAL:-60}
LANGUAGE=${LANGUAGE:-en}
DEBUG_LOGGING=${DEBUG_LOGGING:-FALSE}
IN_MEMORY_DB=${IN_MEMORY_DB:-TRUE}
LIMIT_SENTENCES=${LIMIT_SENTENCES:-FALSE}
ALLOW_UNKNOWN=${ALLOW_UNKNOWN:-FALSE}
CUSTOM_SENTENCES_DIRS=${CUSTOM_SENTENCES_DIRS:-}

if [ "${DEBUG_LOGGING}" == "TRUE" ]; then
    flags+=('--debug')
fi

if [ "${IN_MEMORY_DB}" == "TRUE" ]; then
    flags+=('--in-memory-db')
fi

if [ "${ALLOW_UNKNOWN}" == "TRUE" ]; then
    flags+=('--allow-unknown')
fi

if [ -n "${CUSTOM_SENTENCES_DIRS}" ]; then
    IFS=',' read -ra custom_dirs <<< "${CUSTOM_SENTENCES_DIRS}"
    for custom_dir in "${custom_dirs[@]}"; do
        flags+=('--custom-sentences-dir' "${custom_dir}")
    done
fi

echo STT_URI                =   ${STT_URI}
echo HASS_TOKEN             =   ${HASS_TOKEN}
echo HASS_URI               =   ${HASS_URI}
echo URI                    =   ${URI}
echo CORRECTION_THRESHOLD   =   ${CORRECTION_THRESHOLD}
echo REFRESH_INTERVAL       =   ${REFRESH_INTERVAL}
echo LANGUAGE               =   ${LANGUAGE}
echo DEBUG_LOGGING          =   ${DEBUG_LOGGING}
echo IN_MEMORY_DB           =   ${IN_MEMORY_DB}
echo LIMIT_SENTENCES        =   ${LIMIT_SENTENCES}
echo ALLOW_UNKNOWN          =   ${ALLOW_UNKNOWN}
echo CUSTOM_SENTENCES_DIRS  =   ${CUSTOM_SENTENCES_DIRS}

cd /usr/wyoming_rapidfuzz_proxy

echo flags = ${flags[@]}
python3 -m wyoming_rapidfuzz_proxy \
    --stt-uri ${STT_URI} \
    --hass-token ${HASS_TOKEN} \
    --hass-uri ${HASS_URI} \
    --uri ${URI} \
    --data-dir /data \
    --correction-threshold $CORRECTION_THRESHOLD \
    --refresh-interval $REFRESH_INTERVAL \
    --language $LANGUAGE \
    ${flags[@]}
