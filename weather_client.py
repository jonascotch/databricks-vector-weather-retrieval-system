"""Location-aware client for National Weather Service weather narratives."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from itertools import chain
import logging
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urljoin, urlparse

import requests


NWS_BASE_URL = "https://api.weather.gov"
NWS_USER_AGENT = os.getenv("NWS_USER_AGENT", "")
logger = logging.getLogger(__name__)
MAX_REQUESTS_PER_LOCATION = 20
MAX_HTTP_ATTEMPTS = 3
ALERT_REQUEST_INTERVAL_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 20
DEFAULT_GAZETTEER_PATH = Path(__file__).parent / "data" / "us_places.txt"

_STATE_FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY",
}
_ALERT_LOCK = threading.Lock()
_LAST_ALERT_REQUEST = 0.0
_CACHE_LOCK = threading.Lock()
_JSON_CACHE: dict[str, tuple[float, dict, dict[str, str]]] = {}


class WeatherClientError(Exception):
    """A location or NWS retrieval error safe to report to the API caller."""


class LocationValidationError(WeatherClientError):
    """The supplied location is malformed or cannot be resolved."""


class RequestBudgetExhausted(WeatherClientError):
    """The per-location outbound request budget has been consumed."""


@dataclass
class LocationRequestBudget:
    limit: int = MAX_REQUESTS_PER_LOCATION
    attempts: int = 0
    location_id: str | None = None

    def consume(self) -> None:
        if self.attempts >= self.limit:
            raise RequestBudgetExhausted(
                f"The {self.limit}-request budget was reached for this location."
            )
        self.attempts += 1


@dataclass
class LocationSync:
    documents: list[dict] = field(default_factory=list)
    requests_made: int = 0
    request_budget_exhausted: bool = False
    warnings: list[str] = field(default_factory=list)
    resolved: dict | None = None


class CensusGazetteer:
    """Resolve exact city/state pairs using the downloaded Census Places file."""

    def __init__(self, path: str | Path = DEFAULT_GAZETTEER_PATH):
        self.path = Path(path)
        self._places: list[tuple[str, str, float, float]] | None = None

    def resolve(self, city: str, state: str) -> tuple[float, float]:
        if self._places is None:
            self._places = self._load()

        normalized_city = _normalize_name(city)
        normalized_state = state.strip().upper()
        if len(normalized_state) != 2 or not normalized_state.isalpha():
            raise LocationValidationError("State must be a two-letter abbreviation.")

        matches = [
            (latitude, longitude)
            for name, state_code, latitude, longitude in self._places
            if name == normalized_city and state_code == normalized_state
        ]
        if not matches:
            raise LocationValidationError(
                "City/state was not found in the Census Gazetteer; use coordinates."
            )
        if len(matches) > 1:
            raise LocationValidationError(
                "City/state matches multiple Gazetteer places; use coordinates."
            )
        return matches[0]

    def _load(self) -> list[tuple[str, str, float, float]]:
        if not self.path.is_file():
            raise WeatherClientError(
                "Census Gazetteer file is missing at data/us_places.txt; see the setup guide."
            )

        with self.path.open("r", encoding="utf-8-sig", newline="") as file:
            first_line = file.readline()
            if not first_line:
                raise WeatherClientError("Census Gazetteer file is empty.")
            delimiter = "|" if "|" in first_line else "\t"
            file.seek(0)
            rows = csv.reader(file, delimiter=delimiter)
            first_row = next(rows, [])
            has_header = any(cell.strip().upper() == "NAME" for cell in first_row)
            if has_header:
                header = [cell.strip().upper() for cell in first_row]
                name_index = header.index("NAME")
                geoid_index = header.index("GEOID")
                latitude_index = next(
                    (i for i, value in enumerate(header) if value in ("INTPTLAT", "LATITUDE")),
                    None,
                )
                longitude_index = next(
                    (i for i, value in enumerate(header) if value in ("INTPTLONG", "LONGITUDE")),
                    None,
                )
            else:
                name_index, geoid_index = 1, 0
                latitude_index, longitude_index = len(first_row) - 2, len(first_row) - 1
                rows = chain((first_row,), rows)

            if latitude_index is None or longitude_index is None:
                raise WeatherClientError(
                    "Census Gazetteer is missing internal-point latitude/longitude columns."
                )

            places = []
            for row in rows:
                if max(name_index, geoid_index, latitude_index, longitude_index) >= len(row):
                    continue
                geoid = row[geoid_index].strip()
                state_code = _STATE_FIPS.get(geoid[:2])
                if not state_code:
                    continue
                try:
                    places.append(
                        (
                            _normalize_name(row[name_index]),
                            state_code,
                            float(row[latitude_index]),
                            float(row[longitude_index]),
                        )
                    )
                except (ValueError, IndexError):
                    continue

        if not places:
            raise WeatherClientError("Census Gazetteer contains no usable place records.")
        return places


def _normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _normalize_coordinate(value, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise LocationValidationError(f"{label} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise LocationValidationError(f"{label} must be a number.") from None
    if not minimum <= number <= maximum:
        raise LocationValidationError(
            f"{label} must be between {minimum:g} and {maximum:g}."
        )
    return round(number, 4)


def _iso_or_none(value):
    if not value:
        return None
    return value


def _provider_url_is_valid(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "api.weather.gov"


def _cache_expiration(response: requests.Response) -> float:
    cache_control = response.headers.get("Cache-Control", "")
    match = re.search(r"(?:^|,)\s*max-age=(\d+)", cache_control, flags=re.IGNORECASE)
    if match:
        return time.time() + int(match.group(1))
    expires = response.headers.get("Expires")
    if expires:
        try:
            parsed = parsedate_to_datetime(expires)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (TypeError, ValueError):
            pass
    return time.time()


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, retry_at.timestamp() - time.time())
        except (TypeError, ValueError):
            return None


def _update_json_cache(url: str, response: requests.Response, data: dict) -> None:
    if "no-store" in response.headers.get("Cache-Control", "").lower():
        with _CACHE_LOCK:
            _JSON_CACHE.pop(url, None)
        return
    metadata = {
        key: value
        for key, value in (
            ("etag", response.headers.get("ETag")),
            ("last-modified", response.headers.get("Last-Modified")),
        )
        if value
    }
    with _CACHE_LOCK:
        _JSON_CACHE[url] = (_cache_expiration(response), data, metadata)


def _wait_for_alert_slot() -> None:
    global _LAST_ALERT_REQUEST
    with _ALERT_LOCK:
        now = time.monotonic()
        wait = ALERT_REQUEST_INTERVAL_SECONDS - (now - _LAST_ALERT_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _LAST_ALERT_REQUEST = time.monotonic()


class WeatherClient:
    def __init__(
        self,
        user_agent: str | None = None,
        gazetteer: CensusGazetteer | None = None,
        session: requests.Session | None = None,
    ):
        self.user_agent = (user_agent or NWS_USER_AGENT).strip()
        self.gazetteer = gazetteer or CensusGazetteer()
        self.session = session or requests.Session()

    def resolve_location(self, value) -> dict:
        if isinstance(value, str):
            if "," not in value:
                raise LocationValidationError(
                    "City/state must use the format 'City, ST'."
                )
            city, state = value.rsplit(",", 1)
            city, state = city.strip(), state.strip()
            if not city:
                raise LocationValidationError("City is required.")
            latitude, longitude = self.gazetteer.resolve(city, state)
            return {
                "location": f"{city}, {state.upper()}",
                "latitude": latitude,
                "longitude": longitude,
            }

        if not isinstance(value, dict) or set(value) != {"lat", "lon"}:
            raise LocationValidationError(
                "A location must be a city/state string or an object with lat and lon."
            )
        latitude = _normalize_coordinate(value["lat"], "Latitude", -90, 90)
        longitude = _normalize_coordinate(value["lon"], "Longitude", -180, 180)
        return {
            "location": f"{latitude:.4f}, {longitude:.4f}",
            "latitude": latitude,
            "longitude": longitude,
            "is_coordinates": True,
        }

    def _get_json(
        self,
        url: str,
        budget: LocationRequestBudget,
        *,
        alert_request: bool = False,
    ) -> dict:
        if not self.user_agent or "@" not in self.user_agent:
            logger.warning(
                "Weather request blocked: category=user_agent location_id=%s",
                budget.location_id,
            )
            raise WeatherClientError(
                "NWS_USER_AGENT must identify the application and include a contact address."
            )
        if not _provider_url_is_valid(url):
            logger.warning(
                "Weather request blocked: category=unsupported_url location_id=%s",
                budget.location_id,
            )
            raise WeatherClientError("NWS returned an unsupported service URL.")

        with _CACHE_LOCK:
            cached = _JSON_CACHE.get(url)
        if cached and cached[0] > time.time():
            return cached[1]

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/geo+json, application/json",
        }
        if cached:
            if cached[2].get("etag"):
                headers["If-None-Match"] = cached[2]["etag"]
            if cached[2].get("last-modified"):
                headers["If-Modified-Since"] = cached[2]["last-modified"]

        for attempt in range(MAX_HTTP_ATTEMPTS):
            budget.consume()
            if alert_request:
                _wait_for_alert_slot()
            try:
                response = self.session.get(
                    url,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    allow_redirects=False,
                )
            except requests.RequestException as error:
                if attempt + 1 == MAX_HTTP_ATTEMPTS:
                    logger.warning(
                        "Weather request failed: category=transport location_id=%s error_type=%s",
                        budget.location_id,
                        type(error).__name__,
                    )
                    raise WeatherClientError(
                        "NWS request failed after retry attempts."
                    ) from error
                continue

            if response.status_code == 304 and cached:
                _update_json_cache(url, response, cached[1])
                return cached[1]

            if response.status_code in (301, 302, 307, 308):
                redirect_url = response.headers.get("Location")
                if not redirect_url:
                    raise WeatherClientError("NWS returned a redirect without a target.")
                url = urljoin(url, redirect_url)
                if not _provider_url_is_valid(url):
                    logger.warning(
                        "Weather request failed: category=unsupported_redirect location_id=%s",
                        budget.location_id,
                    )
                    raise WeatherClientError("NWS redirected to an unsupported service URL.")
                cached = None
                headers.pop("If-None-Match", None)
                headers.pop("If-Modified-Since", None)
                continue

            if response.status_code in (429, 500, 502, 503, 504):
                if attempt + 1 == MAX_HTTP_ATTEMPTS:
                    logger.warning(
                        "Weather request failed: category=http location_id=%s status=%d",
                        budget.location_id,
                        response.status_code,
                    )
                    raise WeatherClientError(
                        f"NWS service returned HTTP {response.status_code}."
                    )
                retry_after = _retry_after_seconds(
                    response.headers.get("Retry-After")
                )
                if retry_after is not None:
                    time.sleep(retry_after)
                continue

            if not response.ok:
                logger.warning(
                    "Weather request failed: category=http location_id=%s status=%d",
                    budget.location_id,
                    response.status_code,
                )
                raise WeatherClientError(
                    f"NWS service returned HTTP {response.status_code}."
                )
            try:
                data = response.json()
            except ValueError as error:
                logger.warning(
                    "Weather request failed: category=invalid_json location_id=%s",
                    budget.location_id,
                )
                raise WeatherClientError("NWS returned invalid JSON.") from error

            _update_json_cache(url, response, data)
            return data

        raise WeatherClientError("NWS request failed.")

    def sync_location(self, value, location_id: str | None = None) -> LocationSync:
        resolved = self.resolve_location(value)
        result = LocationSync(
            resolved={
                "latitude": resolved["latitude"],
                "longitude": resolved["longitude"],
            }
        )
        budget = LocationRequestBudget(location_id=location_id)
        try:
            self._fetch_documents(resolved, budget, result)
        except RequestBudgetExhausted:
            result.request_budget_exhausted = True
            logger.info(
                "Weather request budget exhausted: operation=sync location_id=%s attempts=%d",
                location_id,
                budget.attempts,
            )
        finally:
            result.requests_made = budget.attempts
        return result

    def _fetch_documents(
        self, resolved: dict, budget: LocationRequestBudget, result: LocationSync
    ) -> None:
        latitude = resolved["latitude"]
        longitude = resolved["longitude"]
        latitude_text = f"{latitude:.4f}"
        longitude_text = f"{longitude:.4f}"
        point_url = f"{NWS_BASE_URL}/points/{latitude_text},{longitude_text}"
        point_data = self._get_json(point_url, budget)
        point = point_data.get("properties") or {}
        grid_id = point.get("gridId")
        grid_x = point.get("gridX")
        grid_y = point.get("gridY")
        if not grid_id or grid_x is None or grid_y is None:
            raise WeatherClientError("NWS did not return a usable grid point.")
        result.resolved.update(
            {"grid_id": grid_id, "grid_x": grid_x, "grid_y": grid_y}
        )

        alert_url = (
            f"{NWS_BASE_URL}/alerts/active"
            f"?point={latitude_text}%2C{longitude_text}"
        )
        self._fetch_alerts(
            alert_url, budget, result
        )

        forecast_targets = (
            ("daily", point.get("forecast")),
            ("hourly", point.get("forecastHourly")),
        )
        for forecast_type, forecast_url in forecast_targets:
            if not forecast_url:
                result.warnings.append(f"NWS did not provide a {forecast_type} forecast URL.")
                continue
            if not _provider_url_is_valid(forecast_url):
                result.warnings.append(f"NWS provided an unsupported {forecast_type} URL.")
                continue
            try:
                forecast_data = self._get_json(forecast_url, budget)
            except RequestBudgetExhausted:
                raise
            except WeatherClientError as error:
                result.warnings.append(f"{forecast_type.title()} forecast unavailable: {error}")
                continue
            self._normalize_forecast(
                forecast_data,
                result,
                grid_id,
                grid_x,
                grid_y,
                forecast_type,
            )

    def _fetch_alerts(
        self,
        url: str,
        budget: LocationRequestBudget,
        result: LocationSync,
    ) -> None:
        try:
            alerts = self._get_json(url, budget, alert_request=True)
        except RequestBudgetExhausted:
            raise
        except WeatherClientError as error:
            result.warnings.append(f"Active alerts unavailable: {error}")
            return

        for feature in alerts.get("features") or []:
            properties = feature.get("properties") or {}
            description = (properties.get("description") or "").strip()
            instruction = (properties.get("instruction") or "").strip()
            narrative = "\n\n".join(
                part
                for part in (
                    f"Description:\n{description}" if description else "",
                    f"Instruction:\n{instruction}" if instruction else "",
                )
                if part
            )
            if not narrative:
                continue
            alert_id = feature.get("id") or properties.get("id")
            if not alert_id:
                result.warnings.append("Skipped an alert without a stable provider identifier.")
                continue
            result.documents.append(
                self._document(
                    id=str(alert_id),
                    grid_id=None,
                    grid_x=None,
                    grid_y=None,
                    source_type="alert",
                    forecast_type=None,
                    headline=properties.get("headline") or properties.get("event"),
                    narrative_text=narrative,
                    issued_at=_iso_or_none(properties.get("sent")),
                    effective_at=_iso_or_none(properties.get("effective")),
                    period_start=None,
                    period_end=None,
                    payload=feature,
                )
            )

    def _normalize_forecast(
        self,
        forecast: dict,
        result: LocationSync,
        grid_id: str,
        grid_x: int,
        grid_y: int,
        forecast_type: str,
    ) -> None:
        properties = forecast.get("properties") or {}
        skipped_without_narrative = 0
        skipped_without_timestamps = 0
        for period in properties.get("periods") or []:
            narrative = (period.get("detailedForecast") or "").strip()
            if not narrative:
                skipped_without_narrative += 1
                continue
            start = _iso_or_none(period.get("startTime"))
            end = _iso_or_none(period.get("endTime"))
            if not start or not end:
                skipped_without_timestamps += 1
                continue
            stable_id = (
                f"forecast:{grid_id}:{grid_x}:{grid_y}:{forecast_type}:{start}:{end}"
            )
            result.documents.append(
                self._document(
                    id=stable_id,
                    grid_id=grid_id,
                    grid_x=grid_x,
                    grid_y=grid_y,
                    source_type="forecast",
                    forecast_type=forecast_type,
                    headline=period.get("name"),
                    narrative_text=narrative,
                    issued_at=_iso_or_none(properties.get("generatedAt")),
                    effective_at=None,
                    period_start=start,
                    period_end=end,
                    payload=period,
                )
            )
        if skipped_without_narrative:
            result.warnings.append(
                f"Skipped {skipped_without_narrative} {forecast_type} periods without embeddable narrative text."
            )
        if skipped_without_timestamps:
            result.warnings.append(
                f"Skipped {skipped_without_timestamps} {forecast_type} periods without start/end timestamps."
            )

    @staticmethod
    def _document(**fields) -> dict:
        fields["narrative_hash"] = hashlib.sha256(
            fields["narrative_text"].encode("utf-8")
        ).hexdigest()
        fields["synced_at"] = datetime.now(timezone.utc).isoformat()
        return fields
