# Wyoming RapidFuzz Proxy
A Wyoming proxy that applies **RapidFuzz-based sentence correction** to the output of any Wyoming Speech-to-Text (STT) service using speech-to-phrase/Home Assistant sentence candidates.

---

# What is the Wyoming RapidFuzz Proxy and How Does It Work?

The Wyoming RapidFuzz Proxy enables RapidFuzz-based sentence correction with **any** STT service compatible with the Wyoming protocol.

Its goal is to make general-purpose STT output look more like the sentence-oriented output produced by [OHF Voice speech-to-phrase](https://github.com/OHF-voice/speech-to-phrase), so Home Assistant's local NLP can still handle recognized Assist commands while unrecognized/open-ended phrases can fall through to another conversation agent, such as an LLM.

### Mechanism

The proxy operates as a middle layer between Home Assistant and your chosen Wyoming STT service. Its primary function is to:

1.  **Fetch Data:** Connect to Home Assistant upon startup, and again when Home Assistant emits reload/start events, to retrieve the latest Assist-exposed entities, areas, floors, sentence triggers, and ask-question answers.
2.  **Intercept:** Intercept the raw transcribed voice command from the upstream STT service.
3.  **Correct:** Apply the RapidFuzz sentence correction logic against the gathered sentence data.
4.  **Pass Back:** Pass the corrected text back to Home Assistant.

This allows third-party Wyoming STT services (such as Whisper or Microsoft STT) to benefit from a robust correction mechanism without requiring Vosk as the recognizer.

### Communication Flow (Transparent Operation)

The proxy is designed to be **transparent**. It exposes itself as a standard Wyoming STT service, requiring no special setup in Home Assistant beyond adding a regular Wyoming integration.

1.  **Home Assistant to Proxy:** Home Assistant sends audio data to the proxy.
2.  **Proxy to STT:** The proxy immediately forwards the audio data to the configured upstream STT service (e.g., Wyoming Whisper).
3.  **STT to Proxy:** The upstream STT service returns the raw transcribed text.
4.  **Correction:** The proxy intercepts the raw text and applies the RapidFuzz sentence correction.
5.  **Proxy to Home Assistant:** The **corrected** text is sent back to Home Assistant, completing the command.

From Home Assistant's perspective, it is simply communicating with a highly accurate STT service.

---

# Acknowledgements

This project started from [Cheerpipe/wyoming_rapidfuzz_proxy](https://github.com/Cheerpipe/wyoming_rapidfuzz_proxy), which adapted RapidFuzz sentence correction from [Wyoming Vosk](https://github.com/rhasspy/wyoming-vosk). This fork keeps that RapidFuzz correction approach while using [OHF Voice speech-to-phrase](https://github.com/OHF-voice/speech-to-phrase) as the inspiration and source for the bundled sentence templates.

* Special thanks to **synesthesiam** for Wyoming Vosk and the original correction approach that inspired the upstream proxy.
* Thanks to **Cheerpipe** for the upstream Wyoming RapidFuzz Proxy implementation that this fork builds on.
* Thanks to the **OHF Voice speech-to-phrase** project for the sentence-template approach and bundled sentence data used by this fork.
* The containerization approach was partially inspired by the scripts used in **wyoming-vosk-standalone** (https://github.com/dekiesel/wyoming-vosk-standalone).

---

# Prerequisites

* A functional **upstream Wyoming Speech-to-Text service** (e.g., [wyoming-faster-whisper](https://github.com/rhasspy/wyoming-faster-whisper)).
* **Docker**
* **Docker Compose** (or a similar container orchestration tool).

---

# Quick Start

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/Cheerpipe/wyoming_rapidfuzz_proxy
    cd wyoming_rapidfuzz_proxy
    ```
2.  **Build the container image:**
    ```bash
    bash scripts/build.sh
    ```
Optionally you may run build.sh with `--enable-no-gil` parameter to compile, install and use python with NO-GIL support enabled and use Python 3.14.0. Docker image creation will be slower.

3.  **Configure:** Edit the `docker-compose.yaml` file to set your **required** environment variables (`HASS_URI`, `HASS_TOKEN`, `STT_URI`). A sentence YAML file is no longer required; the proxy image bundles pinned speech-to-phrase templates plus live Home Assistant context.
4.  **Optional custom sentences:** If you have custom sentence YAML files, mount them and set `CUSTOM_SENTENCES_DIRS` (see below).
5.  **Run the container:**
    ```bash
    docker compose up -d
    ```
    (Use `-d` for detached execution.)

---

# Bundled speech-to-phrase data

The Docker image downloads [OHF Voice speech-to-phrase](https://github.com/OHF-voice/speech-to-phrase) at build time and copies only its runtime sentence data into the image:

* `/opt/speech-to-phrase/sentences/*.yaml`
* `/opt/speech-to-phrase/shared_lists.yaml`
* `/opt/speech-to-phrase/SPEECH_TO_PHRASE_REF`

The pinned upstream ref is controlled by the Docker build argument `SPEECH_TO_PHRASE_REF`. By default it is pinned in the `Dockerfile`, so normal users do **not** need to mount or configure a built-in sentence path at runtime.

To build with a different pinned ref using the included script:

```bash
SPEECH_TO_PHRASE_REF=<commit-sha> bash scripts/build.sh
```

To intentionally test or use a different speech-to-phrase checkout, mount it and override both paths:

```yaml
environment:
  - BUILTIN_SENTENCES_DIR=/custom-speech-to-phrase/sentences
  - SHARED_LISTS_PATH=/custom-speech-to-phrase/shared_lists.yaml
volumes:
  - /path/to/speech_to_phrase:/custom-speech-to-phrase:ro
```

When running from source without the Docker image, pass equivalent CLI flags:

```bash
python3 -m wyoming_rapidfuzz_proxy \
  --builtin-sentences-dir /path/to/speech_to_phrase/sentences \
  --shared-lists-path /path/to/speech_to_phrase/shared_lists.yaml \
  ...
```

---

# Volumes

The proxy uses bundled speech-to-phrase sentence templates and live Home Assistant data, so a sentence YAML file is no longer required. The `/data` volume is still useful for the sentence database and optional overlays. You do not need to mount `/opt/speech-to-phrase` unless you intentionally want to override the pinned built-in templates.

| Path (Inside Container) | Description | Recommended Host Mount Example |
| :--- | :--- | :--- |
| **/data** | Stores the sentence database. If `/data/<language>.yaml` exists, it is treated as an optional overlay for extra `intents`, `lists`, `expansion_rules`, `no_correct_patterns`, or `unknown_text`. | `./data` |
| custom sentence dir | Optional directory containing Home Assistant/speech-to-phrase style custom sentences, such as `/custom_sentences/en/*.yaml`. Configure with `CUSTOM_SENTENCES_DIRS`. | `/config/custom_sentences:/custom_sentences:ro` |

---

# Sentence Sources

The proxy now builds correction candidates from:

1. Bundled curated templates from [OHF Voice speech-to-phrase](https://github.com/OHF-voice/speech-to-phrase). The Docker image pins these templates at build time and loads them from `/opt/speech-to-phrase` by default. These templates mirror Home Assistant Assist-style commands without expanding the full upstream `home-assistant-intents` corpus.
2. Live Home Assistant context fetched over websocket:
   * Assist-exposed, non-disabled entities and their aliases. Entity candidates are limited to entities returned by `homeassistant/expose_entity/list` with `conversation: true`.
   * areas and floors with aliases
   * sentence trigger sentences from `conversation/sentences/list`
   * `assist_satellite.ask_question` answer sentences from automation/script configs
3. Optional custom sentence directories configured by `CUSTOM_SENTENCES_DIRS`.
4. Optional `/data/<language>.yaml` overlay for advanced users.

The proxy may fetch all Home Assistant states because the websocket API exposes state attributes in bulk, but it only uses attributes for Assist-exposed entity IDs when building the correction context. This keeps correction candidates aligned with the entities Assist can control or query.

Home Assistant does not currently expose the complete merged built-in + custom HassIL intent corpus over websocket. Custom sentence files therefore need to be mounted into the proxy if you want them included.

---

# Configuration (Environment Variables)

| Variable | Description | Example |
| :--- | :--- | :--- |
| **URI** | The URL defining the host and listening port for the **proxy itself**. This is the address Home Assistant connects to. | `tcp://0.0.0.0:10301` |
| **STT_URI** | The connection URI for the **upstream Wyoming STT service** (the one providing the transcription). | `tcp://192.168.1.100:10300` |
| **HASS_URI** | **REQUIRED.** The Home Assistant websocket URI. Used by the proxy to fetch current entities, areas, floors, and conversational triggers. | `ws://homeassistant.local:8123/api/websocket` |
| **HASS_TOKEN** | **REQUIRED.** A Home Assistant **long-lived access token** with sufficient permissions to access the necessary API endpoints (entities, areas, etc.). | `eyJhbGciOiJIUzI1NiI...` |
| **LANGUAGE** | The language code for bundled speech-to-phrase templates and optional custom sentences. | `en` |
| **CORRECTION_THRESHOLD** | The maximum **Levenshtein distance** allowed for a correction to be applied. See the section below for details. | `15` |
| **CUSTOM_SENTENCES_DIRS** | Optional comma-separated directories using Home Assistant/speech-to-phrase custom sentence layout. For English, the proxy looks for `<dir>/en/*.yaml` first, then the language family directory. | `/custom_sentences` |
| **BUILTIN_SENTENCES_DIR** | Optional override for the speech-to-phrase built-in sentence directory. The Docker image already provides this by default. | `/opt/speech-to-phrase/sentences` |
| **SHARED_LISTS_PATH** | Optional override for the speech-to-phrase shared lists file. The Docker image already provides this by default. | `/opt/speech-to-phrase/shared_lists.yaml` |
| **LIMIT_SENTENCES** | If `TRUE`, transcripts that do not match any defined sentence will be discarded. | `FALSE` |
| **ALLOW_UNKNOWN** | If `TRUE` and the STT service reports an `<unk>` token, the proxy can return a specific `unknown_text` (if defined in the YAML) instead of failing. | `FALSE` |
| **DEBUG_LOGGING** | Set to `TRUE` to enable debug-level logging output. | `FALSE` |

---

# Understanding CORRECTION\_THRESHOLD

The correction process uses the **Levenshtein distance** to compare the raw transcribed text from the STT service against the generated correction candidates from speech-to-phrase templates, live Home Assistant context, custom sentence directories, and optional `/data/<language>.yaml` overlays.

The Levenshtein distance is a metric that quantifies how similar two strings are by counting the minimum number of single-character edits (insertions, deletions, or substitutions) required to change one word or phrase into the other. A distance of `0` means the phrases are identical.

The **CORRECTION\_THRESHOLD** variable sets the maximum acceptable Levenshtein distance for a correction to be applied:

* If the distance is **less than or equal** to the threshold, the correction is applied (the closest matching sentence is used).
* If the distance is **greater** than the threshold, the original, raw transcription is preserved.

| Threshold Value | Effect | Risk |
| :--- | :--- | :--- |
| **0** | Disables correction entirely. | None. |
| **Low (e.g., 1-5)** | Allows correction only for minor errors. | May miss valid corrections for longer sentences. |
| **High (e.g., 20+)** | Forces a match even for severely misheard phrases. | Open-ended phrases not in your list may be incorrectly corrected to a pre-defined command. |

**Recommendation:** A practical threshold should be set to a value that allows for correction of small errors without aggressively changing valid, yet uncommon, phrases. You should adjust this value based on the common length of entity and area names in your Home Assistant configuration.

# Disclaimer
This project was developed using extensive AI assistance for generating the final source code.
