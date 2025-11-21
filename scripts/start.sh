#!/bin/bash

VERSION_FILE="./wyoming_rapidfuzz_proxy/VERSION"
APP_VERSION=$(cat ${VERSION_FILE} | tr -d '\n' | tr -d ' ')

echo --------------------------------------------
echo Container Application Name: wyoming-rapidfuzz-proxy
echo Container Application Version: ${APP_VERSION}
echo --------------------------------------------

flags=()

if [ "${DEBUG_LOGGING}" == "TRUE" ]; then
    flags+=('--debug')
fi

if [ "${IN_MEMORY_DB}" == "TRUE" ]; then
    flags+=('--in-memory-db')
fi

if [ "${CORRECTION_THRESHOLD}" == "TRUE" ]; then
    flags+=('--correction-threshold')
fi

if [ "${ALLOW_UNKNOWN}" == "TRUE" ]; then
    flags+=('--allow-unknown')
fi

echo STT_URI                =   ${STT_URI}
echo HASS_TOKEN             =   ${HASS_TOKEN}
echo HASS_URI               =   ${HASS_URI}
echo URI                    =   ${URI}
echo CORRECTION_THRESHOLD   =   ${CORRECTION_THRESHOLD}
echo LANGUAGE               =   ${LANGUAGE}
echo DEBUG_LOGGING          =   ${DEBUG_LOGGING}
echo IN_MEMORY_DB           =   ${IN_MEMORY_DB}
echo LIMIT_SENTENCES        =   ${LIMIT_SENTENCES}
echo ALLOW_UNKNOWN          =   ${ALLOW_UNKNOWN}

cd /usr/wyoming_rapidfuzz_proxy

echo flags = ${flags[@]}
python3 -m wyoming_rapidfuzz_proxy \
    --stt-uri ${STT_URI} \
    --hass-token ${HASS_TOKEN} \
    --hass-uri ${HASS_URI} \
    --uri ${URI} \
    --data-dir /data \
    --correction-threshold $CORRECTION_THRESHOLD \
    --language $LANGUAGE \
    ${flags[@]}