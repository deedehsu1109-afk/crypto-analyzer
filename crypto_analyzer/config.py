import json
import os

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULT_CONFIG = {
    "etherscan_api_key": "",
    "trongrid_api_key": "",
    "theme": "dark",
    "max_transactions": 10000,
}


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        config = DEFAULT_CONFIG.copy()
        config.update(data)
        return config
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
