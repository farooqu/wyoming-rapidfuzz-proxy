# Wyoming RapidFuzz Proxy

A Wyoming STT proxy that applies **RapidFuzz-based sentence correction** using speech-to-phrase/Home Assistant sentence candidates.

It sits between Home Assistant and any upstream Wyoming Speech-to-Text service, forwards audio to that service, then optionally corrects the returned transcript before Home Assistant sees it.

## Why use this?

General-purpose STT engines can produce good transcripts, but Home Assistant Assist works best when the transcript closely matches known sentence patterns, entity names, areas, and aliases.

This proxy is intended to make upstream STT output look more like sentence-oriented output from [OHF Voice speech-to-phrase](https://github.com/OHF-voice/speech-to-phrase):

* recognized Assist-style commands can be corrected into phrases Home Assistant's local NLP understands
* open-ended or unrecognized phrases can be preserved, allowing Home Assistant to fall back to another conversation agent such as an LLM
* Vosk is not required as the recognizer; any Wyoming STT service can be used upstream

## How it works

```text
Home Assistant ──audio──▶ Wyoming RapidFuzz Proxy ──audio──▶ Upstream Wyoming STT
Home Assistant ◀─text──── Wyoming RapidFuzz Proxy ◀─text──── Upstream Wyoming STT
                         │
                         └─ RapidFuzz correction against generated candidates
```

On startup, and again when relevant Home Assistant reload/start events occur, the proxy builds a correction database from:

1. bundled [OHF Voice speech-to-phrase](https://github.com/OHF-voice/speech-to-phrase) templates
2. live Home Assistant context fetched over websocket
3. optional custom sentence directories
4. optional `/data/<language>.yaml` overlays

For each transcript returned by the upstream STT service, the proxy searches the generated candidates and applies the closest correction when it is within the configured Levenshtein-distance threshold.

Home Assistant sees the proxy as a normal Wyoming STT service.

## Quick start

### Prerequisites

* A running upstream Wyoming STT service, such as [wyoming-faster-whisper](https://github.com/rhasspy/wyoming-faster-whisper)
* Docker
* Docker Compose, or a similar container orchestration tool
* A Home Assistant long-lived access token

### Build and run

```bash
git clone https://github.com/farooqu/wyoming-rapidfuzz-proxy
cd wyoming-rapidfuzz-proxy
bash scripts/build.sh
```

Optionally, run `build.sh` with `--enable-no-gil` to compile, install, and use Python 3.14.0 with no-GIL support. This makes Docker image creation slower.

Before starting the container, edit `docker-compose.yaml` and set the required environment variables:

* `HASS_URI`
* `HASS_TOKEN`
* `STT_URI`

A sentence YAML file is not required for normal use. The image bundles pinned speech-to-phrase templates and combines them with live Home Assistant context.

Then start the proxy:

```bash
docker compose up -d
```

## Configuration

| Variable | Description | Example / Default |
| :--- | :--- | :--- |
| `URI` | Address the proxy listens on. This is the Wyoming STT address Home Assistant connects to. | `tcp://0.0.0.0:10301` |
| `STT_URI` | Connection URI for the upstream Wyoming STT service that performs transcription. | `tcp://192.168.1.100:10300` |
| `HASS_URI` | **Required.** Home Assistant websocket URI. | `ws://homeassistant.local:8123/api/websocket` |
| `HASS_TOKEN` | **Required.** Home Assistant long-lived access token with access to the needed websocket APIs. | `eyJhbGciOiJIUzI1NiI...` |
| `LANGUAGE` | Language code for bundled speech-to-phrase templates and optional custom sentences. | `en` |
| `CORRECTION_THRESHOLD` | Maximum Levenshtein distance allowed for a correction. See [Correction threshold](#correction-threshold). | `15` |
| `CUSTOM_SENTENCES_DIRS` | Optional comma-separated directories using Home Assistant/speech-to-phrase custom sentence layout. | `/custom_sentences` |
| `BUILTIN_SENTENCES_DIR` | Optional override for the bundled speech-to-phrase sentence directory. | `/opt/speech-to-phrase/sentences` |
| `SHARED_LISTS_PATH` | Optional override for the bundled speech-to-phrase shared lists file. | `/opt/speech-to-phrase/shared_lists.yaml` |
| `IN_MEMORY_DB` | If `TRUE`, use an in-memory SQLite sentence database instead of a file-backed database under `/data`. | `TRUE` |
| `LIMIT_SENTENCES` | If `TRUE`, transcripts that do not match any defined sentence are discarded. | `FALSE` |
| `ALLOW_UNKNOWN` | If `TRUE` and the STT service reports an `<unk>` token, the proxy can return `unknown_text` if defined in YAML. | `FALSE` |
| `DEBUG_LOGGING` | Enable debug-level logging. | `FALSE` |

## Sentence sources

The proxy builds correction candidates from several sources.

### Bundled speech-to-phrase templates

The Docker image downloads [OHF Voice speech-to-phrase](https://github.com/OHF-voice/speech-to-phrase) at build time and copies only the runtime sentence data into the image:

* `/opt/speech-to-phrase/sentences/*.yaml`
* `/opt/speech-to-phrase/shared_lists.yaml`
* `/opt/speech-to-phrase/SPEECH_TO_PHRASE_REF`

The pinned upstream ref is controlled by the Docker build argument `SPEECH_TO_PHRASE_REF`. The default is pinned in the `Dockerfile`, so normal users do not need to mount or configure a built-in sentence path.

To build with a different pinned speech-to-phrase ref:

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

### Live Home Assistant context

The proxy fetches live Home Assistant data over websocket and uses it to generate context-aware candidates:

* Assist-exposed, non-disabled entities and their aliases
* areas and floors with aliases
* sentence trigger sentences from `conversation/sentences/list`
* `assist_satellite.ask_question` answer sentences from automation/script configs

Entity candidates are limited to entities returned by `homeassistant/expose_entity/list` with `conversation: true`.

The proxy may fetch all Home Assistant states because the websocket API exposes state attributes in bulk, but it only uses attributes for Assist-exposed entity IDs when building the correction context. This keeps correction candidates aligned with the entities Assist can control or query.

Home Assistant does not currently expose the complete merged built-in + custom HassIL intent corpus over websocket. Mount custom sentence files into the proxy if you want them included.

### Custom sentences and overlays

For custom Home Assistant/speech-to-phrase style sentence files, mount a directory and set `CUSTOM_SENTENCES_DIRS`.

For English, the proxy looks for `<dir>/en/*.yaml` first, then the language-family directory. For example:

```yaml
environment:
  - CUSTOM_SENTENCES_DIRS=/custom_sentences
volumes:
  - /config/custom_sentences:/custom_sentences:ro
```

The `/data` volume stores the generated sentence database. If `/data/<language>.yaml` exists, it is treated as an optional advanced overlay for additional `intents`, `lists`, `expansion_rules`, `no_correct_patterns`, or `unknown_text`.

| Path inside container | Description | Recommended host mount example |
| :--- | :--- | :--- |
| `/data` | Sentence database and optional `/data/<language>.yaml` overlay. | `./data` |
| custom sentence dir | Optional Home Assistant/speech-to-phrase style custom sentences. Configure with `CUSTOM_SENTENCES_DIRS`. | `/config/custom_sentences:/custom_sentences:ro` |

## Correction threshold

The correction process uses **Levenshtein distance** to compare the raw transcript from the upstream STT service against generated correction candidates.

Levenshtein distance counts the minimum number of single-character edits needed to change one phrase into another. A distance of `0` means the phrases are identical.

`CORRECTION_THRESHOLD` sets the maximum distance allowed for correction:

* if the distance is less than or equal to the threshold, the closest candidate is returned
* if the distance is greater than the threshold, the original transcript is preserved

| Threshold | Effect | Risk |
| :--- | :--- | :--- |
| `0` | Disables correction. | None. |
| Low, such as `1`-`5` | Corrects only minor transcript errors. | May miss valid corrections for longer sentences. |
| High, such as `20+` | Allows more aggressive correction. | Open-ended phrases may be incorrectly changed to a known command. |

A practical threshold should allow common STT mistakes to be corrected without aggressively changing valid open-ended phrases. Tune it based on the length and similarity of entity, area, and alias names in your Home Assistant setup.

## Acknowledgements

This project started from [Cheerpipe/wyoming_rapidfuzz_proxy](https://github.com/Cheerpipe/wyoming_rapidfuzz_proxy), which adapted RapidFuzz sentence correction from [Wyoming Vosk](https://github.com/rhasspy/wyoming-vosk). This fork keeps that RapidFuzz correction approach while using [OHF Voice speech-to-phrase](https://github.com/OHF-voice/speech-to-phrase) as the inspiration and source for bundled sentence templates.

* Special thanks to **synesthesiam** for Wyoming Vosk and the original correction approach that inspired the upstream proxy.
* Thanks to **Cheerpipe** for the upstream Wyoming RapidFuzz Proxy implementation that this fork builds on.
* Thanks to the **OHF Voice speech-to-phrase** project for the sentence-template approach and bundled sentence data used by this fork.
* The containerization approach was partially inspired by the scripts used in [wyoming-vosk-standalone](https://github.com/dekiesel/wyoming-vosk-standalone).

## Note

This project was developed using extensive AI assistance for generating the final source code.
