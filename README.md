# Wyoming RapidFuzz Proxy
A Wyoming proxy that applies **Vosk RapidFuzz sentence correction** to the output of any Wyoming Speech-to-Text (STT) service.

---

# What is the Wyoming RapidFuzz Proxy and How Does It Work?

The Wyoming RapidFuzz Proxy enables the use of the powerful sentence correction feature (originally from Wyoming Vosk) with **any** STT service compatible with the Wyoming protocol.

### Mechanism

The proxy operates as a middle layer between Home Assistant and your chosen Wyoming STT service. Its primary function is to:

1.  **Fetch Data:** Connect to Home Assistant upon startup to retrieve the latest entities, areas, and conversational sentences.
2.  **Intercept:** Intercept the raw transcribed voice command from the upstream STT service.
3.  **Correct:** Apply the RapidFuzz sentence correction logic against the gathered sentence data.
4.  **Pass Back:** Pass the corrected text back to Home Assistant.

This allows third-party Wyoming STT services (such as Whisper or Microsoft STT) to benefit from a robust correction mechanism that was previously exclusive to Wyoming Vosk.

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

This project is a dedicated wrapper utilizing the RapidFuzz sentence correction logic found in **Wyoming Vosk** (https://github.com/rhasspy/wyoming-vosk).

* Special thanks to **synesthesiam** for developing the core correction code.
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
3.  **Prepare Sentences File:** Create or copy your language's sentence definition file (`<language>.yaml`) into your desired volume path (e.g., `./sentences/en.yaml`). You can use examples from the [Wyoming Vosk repository](https://github.com/rhasspy/wyoming-vosk/tree/master/examples).
4.  **Configure:** Edit the `docker-compose.yaml` file to set your **required** environment variables (`HASS_URI`, `HASS_TOKEN`, `STT_URI`) and volume paths (see sections below).
5.  **Run the container:**
    ```bash
    docker compose up -d
    ```
    (Use `-d` for detached execution.)

---

# Volumes

The proxy requires a volume mount for the sentence definition files.

| Path (Inside Container) | Description | Recommended Host Mount Example |
| :--- | :--- | :--- |
| **/data** | This directory must contain the sentence definition file used for correction, named **`<language>.yaml`** (e.g., `en.yaml`). | `./sentences` |

---

# Configuration (Environment Variables)

| Variable | Description | Example |
| :--- | :--- | :--- |
| **URI** | The URL defining the host and listening port for the **proxy itself**. This is the address Home Assistant connects to. | `tcp://0.0.0.0:10301` |
| **STT_URI** | The connection URI for the **upstream Wyoming STT service** (the one providing the transcription). | `tcp://192.168.1.100:10300` |
| **HASS_URI** | **REQUIRED.** The Home Assistant websocket URI. Used by the proxy to fetch current entities, areas, floors, and conversational triggers. | `ws://homeassistant.local:8123/api/websocket` |
| **HASS_TOKEN** | **REQUIRED.** A Home Assistant **long-lived access token** with sufficient permissions to access the necessary API endpoints (entities, areas, etc.). | `eyJhbGciOiJIUzI1NiI...` |
| **LANGUAGE** | The language code corresponding to the definition file in the `/data` volume (e.g., `en` for `/data/en.yaml`). | `en` |
| **CORRECTION_THRESHOLD** | The maximum **Levenshtein distance** allowed for a correction to be applied. Set to `0` to disable correction entirely. See the section below for details. | `15` |
| **LIMIT_SENTENCES** | If `TRUE`, transcripts that do not match any defined sentence will be discarded. | `FALSE` |
| **ALLOW_UNKNOWN** | If `TRUE` and the STT service reports an `<unk>` token, the proxy can return a specific `unknown_text` (if defined in the YAML) instead of failing. | `FALSE` |
| **DEBUG_LOGGING** | Set to `TRUE` to enable debug-level logging output. | `FALSE` |

---

# Understanding CORRECTION\_THRESHOLD

The correction process uses the **Levenshtein distance** to compare the raw transcribed text from the STT service against the list of pre-defined, correct sentences in your `<language>.yaml` file.

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
