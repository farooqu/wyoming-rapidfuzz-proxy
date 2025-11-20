"""Wyoming RapidFuzz Proxy event handler for STT correction."""
import asyncio
import logging
import time
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStop, AudioStart
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.client import AsyncClient
from .sentences import correct_sentence

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
        sentence_manager,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.wyoming_info_event = wyoming_info.event()
        # Client for the underlying STT service
        self.stt_clientt = AsyncClient.from_uri(stt_uri)
        self.cli_args = cli_args
        # Manager for language configuration
        self.sentence_manager = sentence_manager
        self.model_name = "default"

        # Counter for audio bytes sent per session.
        self.audio_bytes_sent = 0

        # Initialize a bounded queue to control memory usage and provide backpressure
        # to the client when the consumer cannot keep up with the incoming data rate.
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=500)

        # Create a background task to consume and process events from the queue.
        self.consumer_task = asyncio.create_task(self._consumer_loop())

    async def handle_event(self, event: Event) -> bool:
        """
        Handle an incoming Wyoming event by placing it into the processing queue.

        This method acts as the producer. If the queue is full, this coroutine
        will pause, effectively applying backpressure to the client connection.
        """
        start_time = time.monotonic()
        await self.queue.put(event)
        end_time = time.monotonic()
        put_time = end_time - start_time

        # If the wait time for 'put' is significant (e.g., > 0.01 seconds),
        # it means the queue was full and backpressure was activated.
        # Adjust the threshold (0.01) as needed.
        if put_time > 0.01:
             _LOGGER.warning(
                 "Backpressure WARNING: Waited %.4f seconds to put event %s in queue (size: %d/%d)",
                 put_time, event.type, self.queue.qsize(), self.queue.maxsize
             )

        return True

    async def disconnect(self) -> None:
        """Called when the client disconnects."""
        # Cancel the background consumer task and wait for it to finish.
        if self.consumer_task:
            self.consumer_task.cancel()
            try:
                await self.consumer_task
            except asyncio.CancelledError:
                pass

        # We do not call __aexit__ here blindly because the loop manages the
        # connection lifecycle based on Wyoming protocol states.

    # pylint: disable=too-many-nested-blocks,too-many-branches,too-many-statements
    async def _consumer_loop(self) -> None:
        """
        Background task to consume events from the queue and forward them to the STT service.

        This decouples network I/O from processing logic, preventing head-of-line blocking.
        """
        try:
            while True:
                event = await self.queue.get()

                if Transcribe.is_type(event.type):
                    # The Transcribe event marks the start of a new session.
                    # We must open the connection to the underlying STT service here.

                    # Reset the byte counter at the start of a new session.
                    self.audio_bytes_sent = 0

                    _LOGGER.debug("Attempting to connect to STT service...")
                    connect_start_time = time.monotonic()
                    await self.stt_clientt.__aenter__()
                    connect_end_time = time.monotonic()
                    _LOGGER.debug(
                        "STT connection established in %.4f seconds.",
                        connect_end_time - connect_start_time,
                    )

                    # Pass Transcribe event to the STT service
                    await self.stt_clientt.write_event(event)
                    transcribe = Transcribe.from_event(event)
                    _LOGGER.debug("Language set to %s", transcribe.language)

                elif AudioChunk.is_type(event.type):
                    # Count the audio bytes before sending them.
                    chunk = AudioChunk.from_event(event)
                    self.audio_bytes_sent += len(chunk.audio)

                    # Pass audio chunk to the underlying STT service
                    # Assumes connection was opened by a preceding Transcribe event.
                    await self.stt_clientt.write_event(event)

                elif AudioStart.is_type(event.type):
                    # Pass audio start to the underlying STT service
                    await self.stt_clientt.write_event(event)

                elif AudioStop.is_type(event.type):
                    _LOGGER.debug("Audio stopped.")

                    # Print the total amount of audio bytes sent.
                    _LOGGER.debug(
                        "Total audio bytes sent to backend: %.2f kB",
                        self.audio_bytes_sent / 1024.0,
                    )

                    await self.stt_clientt.write_event(event)

                    # Wait for the underlying STT service to return a transcript
                    while True:
                        return_event = await self.stt_clientt.read_event()
                        if return_event is None:
                            _LOGGER.info("Unexpected empty event")
                            break

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

                            # Close the connection to the underlying STT service for this session
                            # This matches the behavior of the original synchronous code.
                            await self.stt_clientt.__aexit__(None, None, None)
                            break

                elif Describe.is_type(event.type):
                    # Describe is a standalone request. Open, process, and close.

                    _LOGGER.debug("Attempting to connect to STT service for Describe...")
                    connect_start_time = time.monotonic()
                    await self.stt_clientt.__aenter__()
                    connect_end_time = time.monotonic()
                    _LOGGER.debug(
                        "STT connection for Describe established in %.4f seconds.",
                        connect_end_time - connect_start_time,
                    )

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

                            # Close connection after info is sent
                            await self.stt_clientt.__aexit__(None, None, None)
                            break

                # Mark the task as done in the queue
                self.queue.task_done()

        except asyncio.CancelledError:
            # Task was cancelled, usually on disconnect
            # Attempt to close the STT client gracefully if it was left open
            try:
                await self.stt_clientt.__aexit__(None, None, None)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        except Exception as e:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Error in event consumer loop: %s", e)
            # If an error occurs, try to reset the connection state
            try:
                await self.stt_clientt.__aexit__(None, None, None)
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    def fix_transcript(self, text: str) -> str:
        """Corrects a transcript using user-provided sentences (synchronous)."""

        lang_config = self.sentence_manager.get_config()

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