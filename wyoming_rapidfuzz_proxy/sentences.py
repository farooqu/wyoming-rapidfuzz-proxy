"""Sentence template loading and RapidFuzz-based correction."""
import argparse
import itertools
import logging
import re
import time
import sqlite3
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union
import asyncio
try:
    from .hass_api import get_hass_info, wait_for_hass_reload_event
except ImportError:
    from hass_api import get_hass_info, wait_for_hass_reload_event

if TYPE_CHECKING:
    from hassil.expression import Expression, Sentence
    from hassil.intents import SlotList

_LOGGER = logging.getLogger()


@dataclass
class LanguageConfig:
    """Language configuration and in-memory sentence storage."""

    # Stores generated sentences as (input_text, output_text) tuples.
    # sentences: List[Tuple[str, str]] = field(default_factory=list)
    db_conn: sqlite3.Connection = field(default=None)
    # Regular expressions for transcripts that should not be corrected.
    no_correct_patterns: List[re.Pattern] = field(default_factory=list)
    # Text to return for unknown sentences if allow_unknown is enabled.
    unknown_text: Optional[str] = None


# pylint: disable=too-many-locals,too-many-branches,too-many-statements
async def load_sentences_for_language(
    sentences_dir: Union[str, Path],
    language: str,
    hass_uri: str,
    hass_token: str,
    in_memory_db: bool = False,
    custom_sentences_dirs: Optional[Sequence[Union[str, Path]]] = None,
    builtin_sentences_dir: Union[str, Path] = "/opt/speech-to-phrase/sentences",
    shared_lists_path: Union[str, Path] = "/opt/speech-to-phrase/shared_lists.yaml",
) -> Optional[LanguageConfig]:
    """Load Home Assistant intents for language with current HA entities.

    Args:
        sentences_dir: Optional directory containing language YAML overlay files.
        language: The language code (e.g., 'en').
        hass_uri: Home Assistant websocket URI.
        hass_token: Home Assistant long-lived access token.
        builtin_sentences_dir: Directory containing speech-to-phrase language YAML files.
        shared_lists_path: speech-to-phrase shared_lists.yaml path.

    Returns:
        A LanguageConfig object or None if Home Assistant data could not be loaded.
    """
    # Fetch info from Home Assistant asynchronously first. Runtime HA context is the
    # source of truth for exposed entities, areas, floors, and sentence triggers.
    _LOGGER.debug("Fetching Home Assistant info from %s...", hass_uri)
    try:
        info = await get_hass_info(hass_token, hass_uri)
        _LOGGER.debug("Got Home Assistant info.")
    except Exception as e:  # pylint: disable=broad-exception-caught
        _LOGGER.error("Failed to get Home Assistant info: %s", e)
        return None

    sentences_yaml = build_sentences_yaml(
        language,
        info,
        custom_sentences_dirs=custom_sentences_dirs,
        builtin_sentences_dir=builtin_sentences_dir,
        shared_lists_path=shared_lists_path,
    )

    # Optionally merge a YAML overlay if present. This keeps backwards
    # compatibility for users who want additional no_correct_patterns,
    # expansion_rules, lists, or custom sentence templates, but the file is no
    # longer required.
    sentences_path = Path(sentences_dir) / f"{language}.yaml"
    if sentences_path.is_file():
        merge_overlay(sentences_yaml, load_yaml_overlay(sentences_path))

    if not sentences_yaml.get("intents"):
        _LOGGER.warning("No sentences loaded for language: %s", language)
        return None

    # Create the configuration object
    Path(sentences_dir).mkdir(parents=True, exist_ok=True)
    if in_memory_db:
        db_conn = sqlite3.connect(":memory:")
    else:
        db_path = Path(sentences_dir) / "sentences.db"
        db_conn = sqlite3.connect(str(db_path))
    
    # Create FTS5 table
    db_conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS sentences USING fts5(input_text, output_text, tokenize='trigram')"
    )

    config = LanguageConfig(db_conn=db_conn)

    # Load "no correct" patterns
    no_correct_patterns = sentences_yaml.get("no_correct_patterns", [])
    for pattern_text in no_correct_patterns:
        config.no_correct_patterns.append(re.compile(pattern_text))

    # Load text to use for unknown sentences
    config.unknown_text = sentences_yaml.get("unknown_text")

    # Generate all possible sentences into the in-memory config object
    generate_sentences(sentences_yaml, config, language=language)

    return config


def load_yaml_overlay(sentences_path: Path) -> Dict[str, Any]:
    """Load optional YAML overlay for a language."""
    try:
        import yaml
    except ImportError as exc:
        raise Exception("pip3 install wyoming-vosk[limited]") from exc  # pylint: disable=broad-exception-raised

    _LOGGER.debug("Loading %s", sentences_path)
    with open(sentences_path, "r", encoding="utf-8") as sentences_file:
        return yaml.safe_load(sentences_file) or {}


def build_sentences_yaml(
    language: str,
    info: Any,
    custom_sentences_dirs: Optional[Sequence[Union[str, Path]]] = None,
    builtin_sentences_dir: Union[str, Path] = "/opt/speech-to-phrase/sentences",
    shared_lists_path: Union[str, Path] = "/opt/speech-to-phrase/shared_lists.yaml",
) -> Dict[str, Any]:
    """Build a hassil-compatible sentence document from speech-to-phrase data."""
    try:
        import yaml
        from hassil import merge_dict
        from .lang_sentences import LanguageData, load_shared_lists
    except ImportError as exc:
        raise Exception("pip3 install wyoming-vosk[limited]") from exc  # pylint: disable=broad-exception-raised

    sentences_path = Path(builtin_sentences_dir) / f"{language}.yaml"
    if not sentences_path.is_file():
        raise ValueError(
            "No speech-to-phrase sentences found for language "
            f"'{language}' at {sentences_path}. Set --builtin-sentences-dir "
            "or BUILTIN_SENTENCES_DIR to a directory containing speech-to-phrase "
            "sentence YAML files."
        )

    _LOGGER.info("Loading speech-to-phrase sentences from %s", sentences_path)
    with open(sentences_path, "r", encoding="utf-8") as sentences_file:
        lang_data = LanguageData.from_dict(yaml.safe_load(sentences_file))

    sentences_yaml = lang_data.to_intents_dict()
    lists = sentences_yaml.setdefault("lists", {})
    for list_name, list_value in info.things.to_lists_dict().items():
        lists[list_name] = list_value

    shared_lists_file_path = Path(shared_lists_path)
    if not shared_lists_file_path.is_file():
        raise ValueError(
            "No speech-to-phrase shared lists found at "
            f"{shared_lists_file_path}. Set --shared-lists-path or "
            "SHARED_LISTS_PATH to the speech-to-phrase shared_lists.yaml file."
        )

    _LOGGER.info("Loading speech-to-phrase shared lists from %s", shared_lists_file_path)
    with open(shared_lists_file_path, "r", encoding="utf-8") as shared_lists_file:
        merge_dict(lists, load_shared_lists(yaml.safe_load(shared_lists_file)))

    merge_custom_sentence_dirs(sentences_yaml, language, custom_sentences_dirs or [])

    # conversation/sentences/list exposes automation sentence triggers; HA does
    # not currently expose the full merged built-in + custom sentence corpus over
    # websocket, so these are the runtime-only sentences available externally.
    if info.things.extra_sentences:
        sentences_yaml.setdefault("intents", {})["ExtraSentences"] = {
            "data": [{"sentences": info.things.extra_sentences}]
        }

    return sentences_yaml


def merge_custom_sentence_dirs(
    sentences_yaml: Dict[str, Any],
    language: str,
    custom_sentences_dirs: Sequence[Union[str, Path]],
) -> None:
    """Merge custom sentence YAML files using speech-to-phrase directory layout."""
    if not custom_sentences_dirs:
        return

    try:
        import yaml
        from hassil import merge_dict
    except ImportError as exc:
        raise Exception("pip3 install wyoming-vosk[limited]") from exc  # pylint: disable=broad-exception-raised

    language_family = language.split("-", maxsplit=1)[0]
    for custom_root in custom_sentences_dirs:
        root_path = Path(custom_root)
        language_dir = root_path / language
        if not language_dir.exists():
            language_dir = root_path / language_family

        if not language_dir.exists():
            _LOGGER.warning("Custom sentences directory not found: %s", language_dir)
            continue

        for sentences_path in sorted(language_dir.glob("*.yaml")):
            _LOGGER.info("Loading custom sentences from %s", sentences_path)
            with open(sentences_path, "r", encoding="utf-8") as sentences_file:
                merge_dict(sentences_yaml, yaml.safe_load(sentences_file) or {})


def merge_overlay(base: Dict[str, Any], overlay: Dict[str, Any]) -> None:
    """Merge optional user YAML into the generated HA sentence document."""
    if not overlay:
        return

    for key in ("sentences", "no_correct_patterns"):
        if overlay.get(key):
            base.setdefault(key, [])
            base[key] = unique_preserve_order([*base[key], *overlay[key]])

    for key in ("lists", "expansion_rules", "responses", "intents"):
        if overlay.get(key):
            base.setdefault(key, {})
            recursive_merge(base[key], overlay[key])

    if overlay.get("unknown_text") is not None:
        base["unknown_text"] = overlay["unknown_text"]


def recursive_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> None:
    """Recursively merge dictionaries in place."""
    for key, value in overlay.items():
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            recursive_merge(base[key], value)
        else:
            base[key] = value


def unique_preserve_order(values: Iterable[str]) -> List[str]:
    """Return unique, truthy strings while preserving order."""
    unique_values: List[str] = []
    seen = set()
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values


# pylint: disable=too-many-locals,too-many-branches,too-many-statements
def generate_sentences(sentences_yaml: Dict[str, Any], config: LanguageConfig, language: str):
    """Generate all possible sentences from templates and populate config."""
    try:
        import hassil.parse_expression
        import hassil.sample
        from hassil.intents import Intents
        from hassil.parse_expression import Sentence
    except ImportError as exc:
        raise Exception("pip3 install wyoming-vosk[limited]") from exc  # pylint: disable=broad-exception-raised

    start_time = time.monotonic()

    intents = Intents.from_dict(sentences_yaml)
    slot_lists = intents.slot_lists
    expansion_rules = intents.expansion_rules

    # Generate possible sentences
    num_sentences = 0
    batch_size = 1000
    batch = []
    
    # Clear existing data if any (important for file-based DB reload)
    config.db_conn.execute("DELETE FROM sentences")

    for intent_info in sentences_yaml.get("intents", {}).values():
        for data in intent_info.get("data", []):
            data_slot_lists = filter_slot_lists_for_context(
                slot_lists,
                data.get("requires_context"),
                data.get("excludes_context"),
            )
            for input_template in data.get("sentences", []):
                output_text: Optional[str] = None

                if not isinstance(input_template, str):
                    input_str_or_list = input_template["in"]
                    output_text = input_template.get("out")
                    input_templates = (
                        [input_str_or_list]
                        if isinstance(input_str_or_list, str)
                        else input_str_or_list
                    )
                else:
                    input_templates = [input_template]

                for input_template_item in input_templates:
                    if has_empty_referenced_list(input_template_item, data_slot_lists):
                        continue

                    if hassil.intents.is_template(input_template_item):
                        input_expression = hassil.parse_expression.parse_sentence(
                            input_template_item
                        )
                        if isinstance(input_expression, Sentence):
                            input_expression = input_expression.expression

                        for input_text, maybe_output_text in sample_expression_with_output(
                            input_expression,
                            slot_lists=data_slot_lists,
                            expansion_rules=expansion_rules,
                            language=language,
                        ):
                            batch.append(
                                (input_text, output_text or maybe_output_text or input_text)
                            )
                            num_sentences += 1
                    else:
                        batch.append(
                            (input_template_item, output_text or input_template_item)
                        )
                        num_sentences += 1

                    if len(batch) >= batch_size:
                        config.db_conn.executemany(
                            "INSERT INTO sentences (input_text, output_text) VALUES (?, ?)",
                            batch,
                        )
                        batch = []

    if batch:
        config.db_conn.executemany(
            "INSERT INTO sentences (input_text, output_text) VALUES (?, ?)",
            batch
        )
    
    config.db_conn.commit()

    end_time = time.monotonic()

    _LOGGER.info(
        "Generated %s sentence(s) in %0.2f second(s)",
        num_sentences,
        end_time - start_time,
    )


def filter_slot_lists_for_context(
    slot_lists: Dict[str, "SlotList"],
    requires_context: Optional[Dict[str, Any]],
    excludes_context: Optional[Dict[str, Any]],
) -> Dict[str, "SlotList"]:
    """Filter context-aware slot values for one sentence block."""
    if not requires_context and not excludes_context:
        return slot_lists

    from hassil.intents import TextSlotList  # pylint: disable=import-outside-toplevel

    filtered: Dict[str, "SlotList"] = {}
    for list_name, slot_list in slot_lists.items():
        if not isinstance(slot_list, TextSlotList):
            filtered[list_name] = slot_list
            continue

        values = []
        for value in slot_list.values:
            context = value.context or {}
            if context and not context_matches(context, requires_context, excludes_context):
                continue
            values.append(value)

        filtered[list_name] = TextSlotList(name=slot_list.name, values=values)

    return filtered


def has_empty_referenced_list(template: str, slot_lists: Dict[str, "SlotList"]) -> bool:
    """Return true if a template references an empty text slot list."""
    from hassil.intents import TextSlotList  # pylint: disable=import-outside-toplevel

    for list_name in re.findall(r"{([^}:]+)(?::[^}]+)?}", template):
        slot_list = slot_lists.get(list_name)
        if isinstance(slot_list, TextSlotList) and not slot_list.values:
            return True

    return False


def context_matches(
    value_context: Dict[str, Any],
    requires_context: Optional[Dict[str, Any]],
    excludes_context: Optional[Dict[str, Any]],
) -> bool:
    """Return true if a slot value context is allowed for a sentence block."""
    if requires_context:
        for key, expected in requires_context.items():
            if isinstance(expected, dict) and expected.get("slot"):
                continue
            actual = value_context.get(key)
            if isinstance(expected, Sequence) and not isinstance(expected, str):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False

    if excludes_context:
        for key, excluded in excludes_context.items():
            actual = value_context.get(key)
            if isinstance(excluded, Sequence) and not isinstance(excluded, str):
                if actual in excluded:
                    return False
            elif actual == excluded:
                return False

    return True


# pylint: disable=too-many-locals,too-many-branches,too-many-nested-blocks
def sample_expression_with_output(
    expression: "Expression",
    slot_lists: "Optional[Dict[str, SlotList]]" = None,
    expansion_rules: "Optional[Dict[str, Sentence]]" = None,
    language: Optional[str] = None,
) -> Iterable[Tuple[str, Optional[str]]]:
    """Sample possible text strings and corresponding output text from an expression.

    This is a modified version of hassil.sample.sample_expression to also
    yield the output text for slot values.
    """
    from hassil.expression import (  # pylint: disable=import-outside-toplevel
        ListReference,
        RuleReference,
        Sequence,
        Alternative,
        Permutation,
        TextChunk,
    )
    import hassil.sample  # pylint: disable=import-outside-toplevel
    from hassil.intents import TextSlotList  # pylint: disable=import-outside-toplevel
    from hassil.errors import MissingListError, MissingRuleError  # pylint: disable=import-outside-toplevel
    from hassil.util import normalize_whitespace  # pylint: disable=import-outside-toplevel

    if isinstance(expression, TextChunk):
        chunk: TextChunk = expression
        yield (chunk.original_text, chunk.original_text)
    elif hasattr(expression, "expression"):
        yield from sample_expression_with_output(
            expression.expression,
            slot_lists,
            expansion_rules,
            language=language,
        )
    elif isinstance(expression, Alternative):
        # Matches (a | b)
        for item in expression.items:
            yield from sample_expression_with_output(
                item,
                slot_lists,
                expansion_rules,
                language=language,
            )
    elif isinstance(expression, Sequence):
        # Matches a b
        seq: Sequence = expression
        # Recursively sample sub-expressions
        seq_sentences = map(
            partial(
                sample_expression_with_output,
                slot_lists=slot_lists,
                expansion_rules=expansion_rules,
                language=language,
            ),
            seq.items,
        )
        # Combine all possible samples from sub-expressions
        sentence_texts = itertools.product(*seq_sentences)
        for sentence_words in sentence_texts:
            # Join input texts and output texts
            yield (
                normalize_whitespace("".join(w[0] for w in sentence_words)),
                normalize_whitespace(
                    "".join(w[1] for w in sentence_words if w[1] is not None)
                ),
            )
    elif isinstance(expression, Permutation):
        # Matches all permutations of the grouped items.
        perm: Permutation = expression
        perm_sentences = [
            list(
                sample_expression_with_output(
                    item,
                    slot_lists,
                    expansion_rules,
                    language=language,
                )
            )
            for item in perm.items
        ]
        for ordered_sentences in itertools.permutations(perm_sentences):
            sentence_texts = itertools.product(*ordered_sentences)
            for sentence_words in sentence_texts:
                yield (
                    normalize_whitespace("".join(w[0] for w in sentence_words)).strip(),
                    normalize_whitespace(
                        "".join(w[1] for w in sentence_words if w[1] is not None)
                    ).strip(),
                )
    elif isinstance(expression, ListReference):
        # {list} reference
        list_ref: ListReference = expression
        if (not slot_lists) or (list_ref.list_name not in slot_lists):
            raise MissingListError(f"Missing slot list {{{list_ref.list_name}}}")

        slot_list = slot_lists[list_ref.list_name]
        if isinstance(slot_list, TextSlotList):
            text_list: TextSlotList = slot_list

            if not text_list.values:
                _LOGGER.warning("No values for list: %s", list_ref.list_name)

            for text_value in text_list.values:
                if text_value.value_out:
                    is_first_text = True
                    # Sample text_in, setting output_text only for the first sample
                    for input_text, output_text in sample_expression_with_output(
                        text_value.text_in,
                        slot_lists,
                        expansion_rules,
                        language=language,
                    ):
                        if is_first_text:
                            output_text = (
                                str(text_value.value_out)
                                if text_value.value_out is not None
                                else ""
                            )
                            is_first_text = False
                        else:
                            output_text = None

                        yield (input_text, output_text)
                else:
                    # If no specific output, yield from text_in
                    yield from sample_expression_with_output(
                        text_value.text_in,
                        slot_lists,
                        expansion_rules,
                        language=language,
                    )
        else:
            for input_text in hassil.sample.sample_expression(
                list_ref,
                slot_lists=slot_lists,
                expansion_rules=expansion_rules,
                language=language,
                expand_ranges=False,
            ):
                yield (input_text, input_text)
    elif isinstance(expression, RuleReference):
        # <rule> reference
        rule_ref: RuleReference = expression
        if (not expansion_rules) or (rule_ref.rule_name not in expansion_rules):
            raise MissingRuleError(f"Missing expansion rule <{rule_ref.rule_name}>")

        rule_body = expansion_rules[rule_ref.rule_name]
        # Recursively sample from the rule body
        yield from sample_expression_with_output(
            rule_body,
            slot_lists,
            expansion_rules,
            language=language,
        )
    else:
        raise ValueError(f"Unexpected expression: {expression}")


def make_trigrams(text: str) -> List[str]:
    """Generate trigrams from text."""
    if len(text) < 3:
        return [text]
    return [text[i : i + 3] for i in range(len(text) - 2)]


def correct_sentence(
    text: str, config: LanguageConfig, score_cutoff: float = 0.0
) -> str:
    """Correct a sentence using rapidfuzz based on generated sentences."""

    if not config.db_conn:
        # Can't correct without database
        return text

    # Nothing to correct
    if not text:
        _LOGGER.debug("Empty transcript")
        return text

    # Don't correct transcripts that match a "no correct" pattern
    for pattern in config.no_correct_patterns:
        if pattern.match(text):
            return text

    try:
        from rapidfuzz.distance import Levenshtein
        from rapidfuzz.process import extractOne
    except ImportError as exc:
        raise Exception("pip3 install wyoming-vosk[limited]") from exc  # pylint: disable=broad-exception-raised

    # Search in SQLite FTS5 first
    # Generate trigrams for the input text
    trigrams = make_trigrams(text)
    # Escape double quotes in trigrams
    safe_trigrams = [t.replace('"', '""') for t in trigrams]
    # Construct OR query
    query = " OR ".join(f'"{t}"' for t in safe_trigrams)
    
    # Get top 50 candidates using trigram matching
    # We select input_text and output_text
    cursor = config.db_conn.execute(
        f"SELECT input_text, output_text FROM sentences WHERE input_text MATCH ? ORDER BY rank LIMIT 50",
        (query,)
    )
    candidates = list(cursor.fetchall())

    if not candidates:
        return text

    # Search in the candidates list
    # processor=lambda s: s[0] uses the input_text part of the tuple for scoring
    result = extractOne(
        [text],  # critical that this is a list
        candidates,
        processor=lambda s: s[0],  # s is (input_text, output_text)
        scorer=Levenshtein.distance,
        scorer_kwargs={"weights": (1, 1, 3)},
    )

    if not result:
        # No match found (should not happen if config.sentences is not empty)
        return text

    fixed_row, score = result[0], result[1]

    final_text = text
    score_pct = score / len(text) if len(text) > 0 else 0

    # Apply correction if score is below or equal to cutoff
    if (score_cutoff <= 0) or (score <= score_cutoff):
        # Map to output text
        # fixed_row is (input, output), we want output (index 1)
        final_text = fixed_row[1]

    if score > score_cutoff:
        _LOGGER.debug("No correction candidate within threshold: %s", final_text)

    _LOGGER.debug(
        "score=%s/%s, scorepct=%.2f%%, original=%s, final=%s",
        score,
        score_cutoff,
        score_pct * 100,
        text,
        final_text
    )

    return final_text



# pylint: disable=too-many-instance-attributes
class SentenceManager:
    """Manages sentence loading and hot-reloading."""

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        sentences_dir: Union[str, Path],
        language: str,
        hass_uri: str,
        hass_token: str,
        in_memory_db: bool = False,
        custom_sentences_dirs: Optional[Sequence[Union[str, Path]]] = None,
        builtin_sentences_dir: Union[str, Path] = "/opt/speech-to-phrase/sentences",
        shared_lists_path: Union[str, Path] = "/opt/speech-to-phrase/shared_lists.yaml",
    ):
        self.sentences_dir = Path(sentences_dir)
        self.language = language
        self.hass_uri = hass_uri
        self.hass_token = hass_token
        self.in_memory_db = in_memory_db
        self.custom_sentences_dirs = custom_sentences_dirs or []
        self.builtin_sentences_dir = Path(builtin_sentences_dir)
        self.shared_lists_path = Path(shared_lists_path)
        self.config: Optional[LanguageConfig] = None
        self._running = False
        self._reload_event_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def start(self):
        """Start the watcher task."""
        # Initial load
        await self._load_and_check()
        self._running = True
        self._reload_event_task = asyncio.create_task(self._reload_event_loop())

    async def stop(self):
        """Stop the watcher task."""
        self._running = False
        if self._reload_event_task:
            self._reload_event_task.cancel()
            try:
                await self._reload_event_task
            except asyncio.CancelledError:
                pass

    def get_config(self) -> Optional[LanguageConfig]:
        """Get the current language configuration."""
        return self.config

    async def _reload_event_loop(self):
        """Refresh when Home Assistant emits reload/start events."""
        while self._running:
            try:
                _LOGGER.info("Listening for Home Assistant reload events")
                reason = await wait_for_hass_reload_event(self.hass_token, self.hass_uri)
                if not self._running:
                    return

                _LOGGER.info("Home Assistant reload event detected: %s", reason)
                await asyncio.sleep(2)
                await self._load_and_check()
            except asyncio.CancelledError:
                raise
            except Exception:  # pylint: disable=broad-exception-caught
                if self._running:
                    _LOGGER.exception("Error in Home Assistant reload event listener")
                    await asyncio.sleep(10)

    async def _load_and_check(self):
        """Reload sentence context from Home Assistant."""
        try:
            async with self._lock:
                _LOGGER.info("Refreshing sentences from Home Assistant...")
                new_config = await load_sentences_for_language(
                    self.sentences_dir,
                    self.language,
                    self.hass_uri,
                    self.hass_token,
                    self.in_memory_db,
                    self.custom_sentences_dirs,
                    self.builtin_sentences_dir,
                    self.shared_lists_path,
                )
                if new_config:
                    old_config = self.config
                    self.config = new_config
                    if old_config and old_config.db_conn:
                        old_config.db_conn.close()
                    _LOGGER.info("Sentences refreshed successfully.")
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Failed to reload sentences")


# -----------------------------------------------------------------------------


async def main() -> None:
    """Entry point for testing sentence loading."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--sentences-dir", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument(
        "--builtin-sentences-dir",
        default="/opt/speech-to-phrase/sentences",
        help="Directory containing speech-to-phrase sentence YAML files",
    )
    parser.add_argument(
        "--shared-lists-path",
        default="/opt/speech-to-phrase/shared_lists.yaml",
        help="Path to speech-to-phrase shared_lists.yaml",
    )
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

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG)

    manager = SentenceManager(
        args.sentences_dir,
        args.language,
        args.hass_uri,
        args.hass_token,
        builtin_sentences_dir=args.builtin_sentences_dir,
        shared_lists_path=args.shared_lists_path,
    )
    await manager.start()

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await manager.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
