"""Test script for hot-reload functionality."""
import asyncio
import logging
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from sentences import SentenceManager  # pylint: disable=import-error

# Mock hass_api to avoid network calls
sys.modules['hass_api'] = MagicMock()
from hass_api import get_hass_info  # pylint: disable=import-error,wrong-import-position
get_hass_info.return_value = None  # Simulate no HA connection for simplicity

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
_LOGGER = logging.getLogger()

# pylint: disable=duplicate-code
async def test_hot_reload():
    """Test the hot-reload functionality of SentenceManager."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        lang = "en"
        yaml_path = tmp_path / f"{lang}.yaml"
        # 1. Create initial file
        _LOGGER.info("Creating initial YAML file...")
        yaml_path.write_text("sentences:\\n  - turn on the light", encoding="utf-8")
        # 2. Start Manager
        manager = SentenceManager(
            tmp_path, lang, "ws://localhost", "token", poll_interval=1.0
        )
        await manager.start()
        # Verify initial load
        config = manager.get_config()
        assert config is not None
        assert len(config.sentences) == 1
        assert config.sentences[0][0] == "turn on the light"
        _LOGGER.info("Initial load verified.")

        # 3. Modify file
        _LOGGER.info("Modifying YAML file...")
        await asyncio.sleep(1.1)  # Wait for poll interval
        yaml_path.write_text(
            "sentences:\\n  - turn off the light", encoding="utf-8"
        )
        # Wait for reload
        _LOGGER.info("Waiting for reload...")
        await asyncio.sleep(1.5)
        # Verify reload
        config = manager.get_config()
        assert config is not None
        assert len(config.sentences) == 1
        assert config.sentences[0][0] == "turn off the light"
        _LOGGER.info("Hot-reload verified!")

        # 4. Touch file without changing content (Hash check verification)
        _LOGGER.info("Touching file without content change...")
        last_hash = manager._file_hash  # pylint: disable=protected-access
        yaml_path.touch()
        await asyncio.sleep(1.5)
        # Verify hash didn't change (internal check). If hash is same,
        # it implies no reload logic triggered.
        assert manager._file_hash == last_hash  # pylint: disable=protected-access
        _LOGGER.info("Hash check verified (no reload on touch).")

        await manager.stop()

if __name__ == "__main__":
    asyncio.run(test_hot_reload())
