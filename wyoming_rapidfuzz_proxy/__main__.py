"""Wyoming RapidFuzz Proxy - STT correction layer using RapidFuzz."""
import asyncio
import argparse
import logging
from functools import partial
from wyoming.info import AsrModel, AsrProgram, Attribution, Info
from wyoming.server import AsyncServer
from .sentences import SentenceManager
from .handler import STTProxyEventHandler

_LOGGER = logging.getLogger()


async def main() -> None:
    """Main entry point for the RapidFuzz STT proxy."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--uri", default="tcp://0.0.0.0:10301", help="unix:// or tcp://"
    )
    parser.add_argument(
        "--stt-uri", default="tcp://127.0.0.1:10300", help="unix:// or tcp://"
    )
    parser.add_argument(
        "--language",
        default="en",
        help=(
            "Set default model language. There must be a sentence definition file "
            "in the --data-dir folder named “[language].yaml” (e.g., “en.yaml”). "
            "(Default=en)"
        ),
    )
    parser.add_argument(
        "--data-dir",
        default="/data",
        help="Directory to store definition file and databases with sentences",
    )

    # Arguments for Home Assistant connection
    parser.add_argument(
        "--hass-uri",
        required=True,
        help="Home Assistant websocket URI (ws://...)"
    )
    parser.add_argument(
        "--hass-token",
        required=True,
        help="Home Assistant long-lived access token"
    )

    parser.add_argument(
        "--correction-threshold",
        nargs="?",
        type=int,
        help=(
            "Sets the maximum Levenshtein distance allowed between an audio "
            "transcription and its closest correction. If the difference is "
            "within the threshold, the correction is applied; otherwise, the "
            "original sentence is kept. Higher thresholds allow more corrections "
            "but may alter open-ended phrases. A value of 0 disables all "
            "corrections. (Default=15)"
        ),
        default=15,
    )
    parser.add_argument(
        "--limit-sentences",
        action="store_true",
        help="Only sentences in [language].yaml can be spoken",
    )
    parser.add_argument(
        "--allow-unknown",
        action="store_true",
        help="Return empty transcript when unknown words are spoken",
    )
    parser.add_argument(
        "--in-memory-db",
        action="store_true",
        default=True, 
        help="Use in-memory SQLite database instead of file-based",
    )
    parser.add_argument("--debug", action="store_true", help="Log DEBUG messages")
    parser.add_argument(
        "--log-format", default=logging.BASIC_FORMAT, help="Format for log messages"
    )

    cli_args = parser.parse_args()

    if cli_args.debug:
        logging.basicConfig(level=logging.DEBUG)
        _LOGGER.info("Log level: DEBUG")
    else:
        logging.basicConfig(level=logging.INFO)
        _LOGGER.info("Log level: INFO")

    _LOGGER.debug(cli_args)

    # Define Wyoming service info
    wyoming_info = Info(
        asr=[
            AsrProgram(
                name="RapidFuzz STT proxy",
                description=(
                    "A speech recognition proxy to add a correction layer "
                    "to any Wyoming STT"
                ),
                attribution=Attribution(
                    name="Felipe Urzúa",
                    url="https://todo",
                ),
                installed=True,
                version=0.1,
                models=[
                    AsrModel(
                        name="wyoming-vosk",
                        description="Sentence correction from wyoming-vosk",
                        attribution=Attribution(
                            name="wyoming-vosk",
                            url="https://github.com/rhasspy/wyoming-vosk",
                        ),
                        installed=True,
                        version=0.1,
                        languages=[cli_args.language],  # GetLangs from source stt
                    )
                ],
            )
        ],
    )


    _LOGGER.info("Loading sentences and connecting to Home Assistant...")
    # Initialize SentenceManager
    sentence_manager = SentenceManager(
        sentences_dir=cli_args.data_dir,
        language=cli_args.language,
        hass_uri=cli_args.hass_uri,
        hass_token=cli_args.hass_token,
        in_memory_db=cli_args.in_memory_db,
    )
    await sentence_manager.start()

    lang_config = sentence_manager.get_config()
    if lang_config:
        _LOGGER.info(
            "Loaded sentences for language '%s'",
            cli_args.language
        )
    else:
        _LOGGER.warning(
            "Could not load sentences for language '%s'. "
            "Correction will be disabled.",
            cli_args.language
        )

    # Initialize Wyoming server
    server = AsyncServer.from_uri(cli_args.uri)

    _LOGGER.info("Ready")

    try:
        # Run the server, passing the pre-loaded config to the handler
        await server.run(
            partial(
                STTProxyEventHandler,
                wyoming_info,
                cli_args.stt_uri,
                cli_args,
                sentence_manager,
            )
        )
    except KeyboardInterrupt:
        pass
    finally:
        await sentence_manager.stop()
    _LOGGER.info("Terminating")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass