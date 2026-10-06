"""Place and airline names for codes. Labels only: searches always use the codes."""
from functools import lru_cache
import json
from pathlib import Path

AIRPORTS = Path(__file__).with_name("airports.json")
AIRLINES = Path(__file__).with_name("airlines.json")


@lru_cache(maxsize=1)
def table():
    return json.loads(AIRPORTS.read_text(encoding="utf-8"))


def supported(code):
    """Whether the flight source accepts this airport code."""
    return code in table()["airports"]


def place(code, display_names=None):
    entry = table()["airports"].get(code)
    city = (display_names or {}).get(code) or (entry[0] if entry else code)
    country = table()["countries"].get(entry[1], "") if entry else ""
    return {"city": city, "country": country}


def city(code, display_names=None):
    return place(code, display_names)["city"]


def places(config):
    return {code: place(code, config.display_names) for code in (*config.origins, *config.destinations)}


@lru_cache(maxsize=1)
def airlines():
    return json.loads(AIRLINES.read_text(encoding="utf-8"))["airlines"]


def airline_supported(code):
    """Whether the flight source can filter by this airline code."""
    return code in airlines()
