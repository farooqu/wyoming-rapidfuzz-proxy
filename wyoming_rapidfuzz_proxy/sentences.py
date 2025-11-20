import argparse
import itertools
import logging
import re
import time
from collections import abc
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set, Tuple, Union
import asyncio
from .hass_api import get_hass_info

if TYPE_CHECKING:
    from hassil.expression import Expression, Sentence
    from hassil.intents import SlotList

_LOGGER = logging.getLogger()


@dataclass
class LanguageConfig:
    """Language configuration and in-memory sentence storage."""

    # Stores generated sentences as (input_text, output_text) tuples.
    sentences: List[Tuple[str, str]] = field(default_factory=list)
    # Regular expressions for transcripts that should not be corrected.
    no_correct_patterns: List[re.Pattern] = field(default_factory=list)
    # Text to return for unknown sentences if allow_unknown is enabled.
    unknown_text: Optional[str] = None


async def load_sentences_for_language(
    sentences_dir: Union[str, Path],
    language: str,
    hass_uri: str,
    hass_token: str,
) -> Optional[LanguageConfig]:
    """Load YAML file for language with sentence templates and HA entities.

    Args:
        sentences_dir: Directory containing language YAML files.
        language: The language code (e.g., 'en').
        hass_uri: Home Assistant websocket URI.
        hass_token: Home Assistant long-lived access token.

    Returns:
        A LanguageConfig object or None if the file is not found.
    """
    sentences_path = Path(sentences_dir) / f"{language}.yaml"
    if not sentences_path.is_file():
        _LOGGER.warning("Sentences file not found: %s", sentences_path)
        return None

    try:
        import yaml
    except ImportError as exc:
        raise Exception("pip3 install wyoming-vosk[limited]") from exc

    # Load and verify YAML
    _LOGGER.debug("Loading %s", sentences_path)
    with open(sentences_path, "r", encoding="utf-8") as sentences_file:
        sentences_yaml = yaml.safe_load(sentences_file)
        if not sentences_yaml:
            _LOGGER.warning("Empty YAML file: %s", sentences_path)
            return None

        if not sentences_yaml.get("sentences"):
            _LOGGER.warning("No sentences in %s", sentences_path)
            return None

    # Fetch info from Home Assistant asynchronously
    _LOGGER.debug("Fetching Home Assistant info from %s...", hass_uri)
    try:
        info = await get_hass_info(hass_token, hass_uri)
        _LOGGER.debug("Got Home Assistant info.")
    except Exception as e:
        _LOGGER.error("Failed to get Home Assistant info: %s", e)
        # Continue without HA info if fetching fails
        info = None

    # Merge sentences yaml with Home Assistant Instance things
    if info:
        grouped_things = {
            "entities": {},
            "areas": [],
            "floors": [],
            "extra_sentences": []
        }

        # Group entities by domain
        entities = [e for e in info.things.entities if e.names]
        for e in entities:
            domain = e.domain
            if not domain:
                continue

            if domain not in grouped_things["entities"]:
                grouped_things["entities"][domain] = []

            for name in e.names:
                if name and name not in grouped_things["entities"][domain]:
                    grouped_things["entities"][domain].append(name)

        grouped_things["entities"] = {
            d: lst for d, lst in grouped_things["entities"].items() if lst
        }

        # Collect Area names
        for a in info.things.areas:
            for name in a.names or []:
                if name and name not in grouped_things["areas"]:
                    grouped_things["areas"].append(name)

        # Collect Floor names
        for f in info.things.floors:
            for name in f.names or []:
                if name and name not in grouped_things["floors"]:
                    grouped_things["floors"].append(name)

        # Collect Extra Sentences (from automations/scripts)
        for s in info.things.extra_sentences:
            if s and s not in grouped_things["extra_sentences"]:
                grouped_things["extra_sentences"].append(s)

        # Ensure 'lists' node exists
        if "lists" not in sentences_yaml:
            sentences_yaml["lists"] = {}

        # Populate slot lists from HA entities/areas/floors
        sentences_yaml["lists"]["light"] = grouped_things["entities"].get("light", [])
        sentences_yaml["lists"]["media_player"] = grouped_things["entities"].get(
            "media_player", []
        )
        sentences_yaml["lists"]["scene"] = grouped_things["entities"].get("scene", [])
        sentences_yaml["lists"]["switch"] = grouped_things["entities"].get("switch", [])
        sentences_yaml["lists"]["climate"] = grouped_things["entities"].get(
            "climate", []
        )
        sentences_yaml["lists"]["vacuum"] = grouped_things["entities"].get("vacuum", [])
        sentences_yaml["lists"]["area"] = grouped_things["areas"]
        sentences_yaml["lists"]["areas"] = grouped_things["areas"]

        # Add extra sentences to the root "sentences" node
        sentences_yaml.setdefault("sentences", [])
        sentences_yaml["sentences"].extend(grouped_things["extra_sentences"])
    else:
        _LOGGER.warning("Skipping Home Assistant entity loading.")

    # Create the configuration object
    config = LanguageConfig()

    # Load "no correct" patterns
    no_correct_patterns = sentences_yaml.get("no_correct_patterns", [])
    for pattern_text in no_correct_patterns:
        config.no_correct_patterns.append(re.compile(pattern_text))

    # Load text to use for unknown sentences
    config.unknown_text = sentences_yaml.get("unknown_text")

    # Generate all possible sentences into the in-memory config object
    generate_sentences(sentences_yaml, config)

    return config


def generate_sentences(sentences_yaml: Dict[str, Any], config: LanguageConfig):
    """Generate all possible sentences from templates and populate config."""
    try:
        import hassil.parse_expression
        import hassil.sample
        from hassil.intents import SlotList, TextChunk, TextSlotList, TextSlotValue
        from hassil.parse_expression import Sentence
    except ImportError as exc:
        raise Exception("pip3 install wyoming-vosk[limited]") from exc

    start_time = time.monotonic()

    templates = sentences_yaml["sentences"]

    # Load slot lists from YAML and HA info
    slot_lists: Dict[str, SlotList] = {}
    for slot_name, slot_info in sentences_yaml.get("lists", {}).items():
        if isinstance(slot_info, abc.Sequence):
            slot_info = {"values": slot_info}

        slot_values = slot_info.get("values")
        if not slot_values:
            _LOGGER.warning("No values for list %s, skipping", slot_name)
            continue

        slot_list_values: List[TextSlotValue] = []
        for slot_value in slot_values:
            values_in: List[str] = []
            value_out: str

            if isinstance(slot_value, str):
                values_in.append(slot_value)
                value_out = slot_value
            else:
                value_in = slot_value["in"]
                value_out = slot_value["out"]

                if hassil.intents.is_template(value_in):
                    input_expression = hassil.parse_expression.parse_sentence(
                        value_in
                    )
                    if isinstance(input_expression, Sentence):
                        input_expression = input_expression.expression

                    for input_text in hassil.sample.sample_expression(
                        input_expression,
                    ):
                        values_in.append(input_text)
                else:
                    values_in.append(value_in)

            for value_in in values_in:
                slot_list_values.append(
                    TextSlotValue(TextChunk(value_in), value_out=value_out)
                )

        slot_lists[slot_name] = TextSlotList("name", slot_list_values)

    # Load expansion rules
    expansion_rules: Dict[str, "Sentence"] = {}
    for rule_name, rule_text in sentences_yaml.get("expansion_rules", {}).items():
        # Ensure we use the correct parse function (Sentence is a type alias)
        import hassil.parse_expression
        
        parsed_rule = hassil.parse_expression.parse_sentence(
            rule_text
        )
        if isinstance(parsed_rule, Sentence):
            parsed_rule = parsed_rule.expression

        expansion_rules[rule_name] = parsed_rule

    # Generate possible sentences
    num_sentences = 0

    for template in templates:
        if isinstance(template, str):
            input_templates: List[str] = [template]
            output_text: Optional[str] = None
        else:
            input_str_or_list = template["in"]
            if isinstance(input_str_or_list, str):
                input_templates = [input_str_or_list]
            else:
                input_templates = input_str_or_list

            output_text = template.get("out")

        for input_template in input_templates:
            if hassil.intents.is_template(input_template):
                input_expression = hassil.parse_expression.parse_sentence(
                    input_template
                )
                if isinstance(input_expression, Sentence):
                    input_expression = input_expression.expression

                for input_text, maybe_output_text in sample_expression_with_output(
                    input_expression,
                    slot_lists=slot_lists,
                    expansion_rules=expansion_rules,
                ):
                    # Add generated sentence to in-memory list
                    config.sentences.append(
                        (input_text, output_text or maybe_output_text or input_text)
                    )
                    num_sentences += 1
            else:
                # Direct sentence (no template)
                config.sentences.append(
                    (input_template, output_text or input_template)
                )
                num_sentences += 1

    end_time = time.monotonic()

    _LOGGER.info(
        "Generated %s sentence(s) in %0.2f second(s)",
        num_sentences,
        end_time - start_time,
    )


def sample_expression_with_output(
    expression: "Expression",
    slot_lists: "Optional[Dict[str, SlotList]]" = None,
    expansion_rules: "Optional[Dict[str, Sentence]]" = None,
) -> Iterable[Tuple[str, Optional[str]]]:
    """Sample possible text strings and corresponding output text from an expression.

    This is a modified version of hassil.sample.sample_expression to also
    yield the output text for slot values.
    """
    from hassil.expression import (
        ListReference,
        RuleReference,
        Sequence,
        Alternative,
        TextChunk,
    )
    from hassil.intents import TextSlotList
    from hassil.errors import MissingListError, MissingRuleError
    from hassil.util import normalize_whitespace

    if isinstance(expression, TextChunk):
        chunk: TextChunk = expression
        yield (chunk.original_text, chunk.original_text)
    elif isinstance(expression, Alternative):
        # Matches (a | b)
        for item in expression.items:
            yield from sample_expression_with_output(
                item,
                slot_lists,
                expansion_rules,
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
                    )
        else:
            raise ValueError(f"Unexpected slot list type: {slot_list}")
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
        )
    else:
        raise ValueError(f"Unexpected expression: {expression}")


def correct_sentence(
    text: str, config: LanguageConfig, score_cutoff: float = 0.0
) -> str:
    """Correct a sentence using rapidfuzz based on generated sentences."""

    if not config.sentences:
        # Can't correct without sentences
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
        raise Exception("pip3 install wyoming-vosk[limited]") from exc

    # Search in the in-memory list of generated sentences
    # processor=lambda s: s[0] uses the input_text part of the tuple for scoring
    result = extractOne(
        [text],  # critical that this is a list
        config.sentences,
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
        _LOGGER.warning("Sentence not recognized: %s", final_text)

    _LOGGER.debug(
        "score=%s/%s, scorepct=%.2f%%, original=%s, final=%s",
        score,
        score_cutoff,
        score_pct * 100,
        text,
        final_text
    )

    return final_text


# -----------------------------------------------------------------------------


async def main() -> None:
    """Entry point for testing sentence loading."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--sentences-dir", required=True)
    parser.add_argument("--language", required=True)
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

    await load_sentences_for_language(
        args.sentences_dir,
        args.language,
        args.hass_uri,
        args.hass_token
    )


if __name__ == "__main__":
    asyncio.run(main())
