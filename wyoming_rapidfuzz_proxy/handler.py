import logging
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStop, AudioStart
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.client import AsyncClient
from .sentences import correct_sentence, LanguageConfig
from typing import Optional

_LOGGER = logging.getLogger()

# Placeholders for unknown token handling.
_DEFAULT_UNK = "<unk>"
UNK_FOR_MODEL = {}


class STTProxyEventHandler(AsyncEventHandler):
    """Event handler for clients connecting to the STT proxy."""

    def __init__(
        self,
        wyoming_info: Info,
        stt_uri: str,
        cli_args,
        lang_config: Optional[LanguageConfig],
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.wyoming_info_event = wyoming_info.event()
        # Client for the underlying STT service
        self.stt_clientt = AsyncClient.from_uri(stt_uri)
        self.cli_args = cli_args
        # Pre-loaded language configuration with sentences
        self.lang_config = lang_config
        self.model_name = "default"

    async def handle_event(self, event: Event) -> bool:
        """Handle an incoming Wyoming event."""
        if AudioChunk.is_type(event.type):
            # Pass audio chunk to the underlying STT service
            await self.stt_clientt.write_event(event)
            return True

        if AudioStart.is_type(event.type):
            # Pass audio start to the underlying STT service
            await self.stt_clientt.write_event(event)
            return True

        if AudioStop.is_type(event.type):
            _LOGGER.debug("Audio stopped.")
            await self.stt_clientt.write_event(event)
            # Wait for the underlying STT service to return a transcript
            while True:
                return_event = await self.stt_clientt.read_event()
                if return_event is None:
                    _LOGGER.info("Unexpected empty event")
                    return True
                if Transcript.is_type(return_event.type):
                    # Process the transcript
                    text = return_event.data["text"].lower().removesuffix(".")
                    original_text = text

                    if self.cli_args.correction_threshold is not None:
                        # Correct the transcript using RapidFuzz
                        text = self.fix_transcript(original_text)
                        if text != original_text:
                            _LOGGER.info(
                                "Original: " + original_text + " Corrected: " + text
                            )

                    _LOGGER.info("Sent: %s", text)
                    # Send the (corrected) transcript back to the client
                    await self.write_event(Transcript(text=text).event())
                    _LOGGER.debug("Completed request")
                    # Close the connection to the underlying STT service
                    await self.stt_clientt.__aexit__(None, None, None)
                    return False

        if Transcribe.is_type(event.type):
            # Start connection to the underlying STT service
            await self.stt_clientt.__aenter__()
            # Pass Transcribe event to the STT service
            await self.stt_clientt.write_event(event)
            transcribe = Transcribe.from_event(event)
            _LOGGER.debug("Language set to %s", transcribe.language)
            return True

        if Describe.is_type(event.type):
            # Start connection and pass Describe event to the STT service
            await self.stt_clientt.__aenter__()
            await self.stt_clientt.write_event(event)
            # Wait for Info event from STT service
            while True:
                return_event = await self.stt_clientt.read_event()
                if Info.is_type(return_event.type):
                    # Modify the name to indicate RapidFuzz correction
                    return_event.data["asr"][0]["name"] = (
                        return_event.data["asr"][0]["name"] + " with RapidFuzz"
                    )
                    await self.write_event(return_event)
                    _LOGGER.debug("Sent info")
                    await self.stt_clientt.__aexit__(None, None, None)
                    return True
        return True

    def fix_transcript(self, text: str) -> str:
        """Corrects a transcript using user-provided sentences (synchronous)."""

        lang_config = self.lang_config

        # Check for unknown words and handle based on CLI arg
        if self.cli_args.allow_unknown and self._has_unknown(text):
            if lang_config is not None:
                return lang_config.unknown_text or ""

            return ""

        if lang_config is None:
            # Cannot correct if no language config was loaded
            return text

        # Perform the synchronous sentence correction
        return correct_sentence(
            text, lang_config, score_cutoff=self.cli_args.correction_threshold
        )

    def _has_unknown(self, text: str) -> bool:
        """Return true if text contains the unknown token for the model."""
        unk_token = UNK_FOR_MODEL.get(self.model_name, _DEFAULT_UNK)
        return (text == unk_token) or (unk_token in text.split())