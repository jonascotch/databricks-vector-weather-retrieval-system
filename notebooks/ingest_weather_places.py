"""Load U.S. Census Gazetteer places into weather.weather_places."""

import csv
import io
import logging
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from lakebase import get_connection
from weather_client import _STATE_FIPS, _normalize_name


GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "2026_Gazetteer/2026_Gaz_place_national.zip"
)
GAZETTEER_MEMBER = "2026_Gaz_place_national.txt"

# Legal name suffixes dropped for autocomplete labels.
_PLACE_SUFFIXES = {
    "city", "town", "village", "borough", "cdp", "uta",
    "municipality", "municipio", "comunidad", "urbana",
}
# LSAD codes for incorporated places: city (25), town (43), village (47).
_INCORPORATED_LSAD = {"25", "43", "47"}

logger = logging.getLogger(__name__)


def _clean_name(full: str) -> str:
    parts = full.split()
    if len(parts) > 1 and parts[-1].casefold() in _PLACE_SUFFIXES:
        return " ".join(parts[:-1])
    return full


def _parse_places(archive: bytes) -> list[tuple[str, str, str, str, float, float]]:
    """Parse the Gazetteer places release into deduplicated place records."""
    member = zipfile.ZipFile(io.BytesIO(archive)).read(GAZETTEER_MEMBER)
    text = member.decode("utf-8-sig")

    first_line = text.splitlines()[0]
    delimiter = "|" if "|" in first_line else "\t"
    rows = csv.reader(text.splitlines(), delimiter=delimiter)
    header = [cell.strip().upper() for cell in next(rows)]
    name_index = header.index("NAME")
    geoid_index = header.index("GEOID")
    lsad_index = header.index("LSAD")
    latitude_index = next(
        i for i, value in enumerate(header) if value in ("INTPTLAT", "LATITUDE")
    )
    longitude_index = next(
        i for i, value in enumerate(header) if value in ("INTPTLONG", "LONGITUDE")
    )

    best: dict[tuple[str, str], tuple] = {}
    for row in rows:
        if max(name_index, geoid_index, lsad_index,
               latitude_index, longitude_index) >= len(row):
            continue
        geoid = row[geoid_index].strip()
        state = _STATE_FIPS.get(geoid[:2])
        if not state:
            continue
        name = _clean_name(row[name_index].strip())
        if not name:
            continue
        try:
            latitude = float(row[latitude_index])
            longitude = float(row[longitude_index])
        except ValueError:
            continue
        key = (_normalize_name(name), state)
        incorporated = row[lsad_index].strip() in _INCORPORATED_LSAD
        entry = (geoid, name, key[0], state, latitude, longitude, incorporated)
        previous = best.get(key)
        if (
            previous is None
            or (incorporated and not previous[6])
            or (incorporated == previous[6] and geoid < previous[0])
        ):
            best[key] = entry

    return [
        (geoid, name, normalized, state, latitude, longitude)
        for geoid, name, normalized, state, latitude, longitude, _ in best.values()
    ]


def run() -> dict:
    """Download the Gazetteer release and refresh weather.weather_places."""
    response = requests.get(GAZETTEER_URL, timeout=300)
    response.raise_for_status()
    records = _parse_places(response.content)
    if not records:
        raise RuntimeError("The Gazetteer release contained no usable place records.")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS weather.weather_places (
                    geoid TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_normalized TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state ~ '^[A-Z]{2}$'),
                    latitude DOUBLE PRECISION NOT NULL,
                    longitude DOUBLE PRECISION NOT NULL,
                    CONSTRAINT weather_places_name_state_key
                        UNIQUE (name_normalized, state)
                )
                """
            )
            cursor.execute("TRUNCATE weather.weather_places")
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            for record in sorted(records, key=lambda item: (item[2], item[3], item[0])):
                writer.writerow(record)
            buffer.seek(0)
            cursor.copy_expert(
                "COPY weather.weather_places "
                "(geoid, name, name_normalized, state, latitude, longitude) "
                "FROM STDIN WITH (FORMAT csv)",
                buffer,
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS weather_places_name_prefix_idx
                ON weather.weather_places (name_normalized text_pattern_ops)
                """
            )
            cursor.execute("SELECT count(*) AS count FROM weather.weather_places")
            total = cursor.fetchone()["count"]

    logger.info("Places load complete: total=%d", total)
    return {"places_loaded": total}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run()
