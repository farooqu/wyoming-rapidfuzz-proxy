import asyncio
import argparse
import logging
from functools import partial
from wyoming.info import AsrModel, AsrProgram, Attribution, Info
from wyoming.server import AsyncServer
from wyoming.info import Info
# Se elimina AsyncClient de aquí, ya no es necesario
from .sentences import load_sentences_for_language, LanguageConfig
from .handler import STTProxyEventHandler

_LOGGER = logging.getLogger()


async def main() -> None:
    """Main entry point."""

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
        help="Set default model language. There must be a sentence definition file in the --dara-dir folder named “[language].yaml” (e.g., “en.yaml”). (Default=en)",
    )
    parser.add_argument(
        "--data-dir",
        default="/data",
        help="Directory to store definition file and databases with sentences",
    )
    
    # --- INICIO DE CAMBIOS ---
    
    # Argumentos añadidos para la conexión con Home Assistant
    parser.add_argument(
        "--hass-uri",
        # Se elimina el valor 'default'
        required=True, # <--- CAMBIO: Se hace obligatorio
        help="Home Assistant websocket URI (ws://...)"
    )
    parser.add_argument(
        "--hass-token",
        required=True,
        help="Home Assistant long-lived access token"
    )

    # --- FIN DE CAMBIOS ---
    
    parser.add_argument(
        "--correction-threshold",
        nargs="?",
        type=int,
        help="Sets the maximum Levenshtein distance allowed between an audio transcription and its closest correction. If the difference is within the threshold, the correction is applied; otherwise, the original sentence is kept. Higher thresholds allow more corrections but may alter open-ended phrases. A value of 0 disables all corrections. (Default=15)",
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
    #
    parser.add_argument("--debug", action="store_true", help="Log DEBUG messages")
    parser.add_argument(
        "--log-format", default=logging.BASIC_FORMAT, help="Format for log messages"
    )

    cli_args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if cli_args.debug else logging.INFO,
        format=cli_args.log_format,
    )
    _LOGGER.debug(cli_args)

    wyoming_info = Info(
        asr=[
            AsrProgram(
                name="RapidFuzz STT proxy",
                description="A speech recognition proxy to add a correction layer to any Wyoming STT",
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
    # Se cargan las frases UNA SOLA VEZ al inicio.
    lang_config = await load_sentences_for_language(
        sentences_dir=cli_args.data_dir,
        language=cli_args.language,
        hass_uri=cli_args.hass_uri,
        hass_token=cli_args.hass_token,
    )
    
    if lang_config:
        _LOGGER.info(f"Loaded {len(lang_config.sentences)} sentences for language '{cli_args.language}'")
    else:
        _LOGGER.warning(f"Could not load sentences for language '{cli_args.language}'. Correction will be disabled.")

    # Se elimina la creación de stt_client
    server = AsyncServer.from_uri(cli_args.uri)

    _LOGGER.info("Ready")

    try:
        # Se pasa el objeto lang_config (con las frases en memoria) al manejador.
        await server.run(
            partial(STTProxyEventHandler, wyoming_info, cli_args.stt_uri, cli_args, lang_config)
        )
    except KeyboardInterrupt:
        pass
    _LOGGER.info("Terminating")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass