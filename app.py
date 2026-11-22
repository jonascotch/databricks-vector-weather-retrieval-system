"""Flask entry point for shared weather synchronization and semantic search."""

from functools import lru_cache
import logging
import math
import os
import re
from uuid import UUID

from flask import Flask, jsonify, render_template, request
import psycopg2

from lakebase import (
    SavedLocationNotFoundError,
    create_saved_location,
    delete_saved_location,
    list_saved_locations,
    search_weather_embeddings,
    search_weather_places,
    update_saved_location_resolution,
    upsert_weather_documents,
)
from weather_client import (
    LocationValidationError,
    WeatherClient,
    WeatherClientError,
)
from weather_embeddings import (
    MODEL_NAME,
    MODEL_REVISION,
    encode_query,
)


app = Flask(__name__)
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@lru_cache(maxsize=1)
def get_weather_client() -> WeatherClient:
    return WeatherClient()


def _error(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def _location_response(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "input": row["input_value"],
        "display_location": row["display_location"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "grid_id": row["grid_id"],
        "grid_x": row["grid_x"],
        "grid_y": row["grid_y"],
    }


def _search_match_response(row: dict) -> dict:
    response = dict(row)
    for field in (
        "synced_at",
        "issued_at",
        "effective_at",
        "period_start",
        "period_end",
    ):
        value = response[field]
        response[field] = value.isoformat() if value is not None else None
    response["similarity"] = float(response["similarity"])
    return response


def _parse_location_input(value) -> tuple[str, object, str, dict | None]:
    if isinstance(value, str):
        parts = value.split(",")
        if len(parts) != 2:
            raise LocationValidationError(
                "Enter a city and two-letter state abbreviation, such as Chicago, IL."
            )
        city, state = parts[0].strip(), parts[1].strip()
        if len(city) < 1 or len(value.strip()) < 3 or not re.fullmatch(r"[A-Za-z]{2}", state):
            raise LocationValidationError(
                "Enter a city and two-letter state abbreviation, such as Chicago, IL."
            )
        canonical = f"{city}, {state.upper()}"
        return "city_state", canonical, canonical, None

    if isinstance(value, dict) and set(value) == {"name", "state", "lat", "lon"}:
        name = value["name"]
        state = value["state"]
        if not isinstance(name, str) or not name.strip():
            raise LocationValidationError("Place name must be a non-empty string.")
        if not isinstance(state, str) or not re.fullmatch(
            r"[A-Za-z]{2}", state.strip()
        ):
            raise LocationValidationError("State must be a two-letter abbreviation.")
        place_coordinates = {}
        for key, label, minimum, maximum in (
            ("lat", "Latitude", -90, 90),
            ("lon", "Longitude", -180, 180),
        ):
            raw_number = value[key]
            if isinstance(raw_number, bool) or not isinstance(raw_number, (int, float)):
                raise LocationValidationError(f"{label} must be a number.")
            try:
                number = float(raw_number)
            except OverflowError:
                raise LocationValidationError(f"{label} must be a finite number.") from None
            if not math.isfinite(number) or not minimum <= number <= maximum:
                raise LocationValidationError(
                    f"{label} must be between {minimum:g} and {maximum:g}."
                )
            place_coordinates[key] = round(number, 4)
        display = f"{' '.join(name.split())}, {state.strip().upper()}"
        return (
            "coordinates",
            place_coordinates,
            display,
            {"latitude": place_coordinates["lat"], "longitude": place_coordinates["lon"]},
        )

    if not isinstance(value, dict) or set(value) != {"lat", "lon"}:
        raise LocationValidationError(
            "Location must be a city/state string or an object with lat and lon."
        )
    coordinates = {}
    for key, label, minimum, maximum in (
        ("lat", "Latitude", -90, 90),
        ("lon", "Longitude", -180, 180),
    ):
        raw_number = value[key]
        if isinstance(raw_number, bool) or not isinstance(raw_number, (int, float)):
            raise LocationValidationError(f"{label} must be a number.")
        try:
            number = float(raw_number)
        except OverflowError:
            raise LocationValidationError(f"{label} must be a finite number.") from None
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise LocationValidationError(
                f"{label} must be between {minimum} and {maximum}."
            )
        coordinates[key] = round(number, 4)
    label = f"{coordinates['lat']:.4f}, {coordinates['lon']:.4f}"
    return "coordinates", coordinates, label, None


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/weather/locations")
def saved_locations():
    try:
        locations = list_saved_locations()
    except psycopg2.Error as error:
        logger.error(
            "Saved-location list failed: category=database error_type=%s",
            type(error).__name__,
        )
        return _error("database_unavailable", "The location list is unavailable.", 503)
    return jsonify({"locations": [_location_response(row) for row in locations]})


@app.get("/weather/places")
def place_suggestions():
    normalized = " ".join(
        request.args.get("q", "").casefold().split()
    )
    if len(normalized) < 2:
        return jsonify({"places": []})
    try:
        places = search_weather_places(normalized, 8)
    except psycopg2.Error as error:
        logger.error(
            "Place suggestions failed: category=database error_type=%s",
            type(error).__name__,
        )
        return _error("database_unavailable", "Place suggestions are unavailable.", 503)
    return jsonify(
        {
            "places": [
                {
                    "name": place["name"],
                    "state": place["state"],
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                }
                for place in places
            ]
        }
    )


@app.post("/weather/locations")
def add_saved_location():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"location"}:
        return _error("invalid_request", "Provide a location value.", 400)
    try:
        input_type, input_value, display_location, resolution = _parse_location_input(
            body["location"]
        )
    except LocationValidationError as error:
        return _error("invalid_location", str(error), 400)

    try:
        row = create_saved_location(input_type, input_value, display_location)
        if resolution is not None:
            row = update_saved_location_resolution(row["id"], resolution)
    except psycopg2.Error as error:
        logger.error(
            "Saved-location creation failed: category=database error_type=%s",
            type(error).__name__,
        )
        return _error("database_unavailable", "The location could not be saved.", 503)
    logger.info(
        "Saved location created: operation=create location_id=%s",
        row["id"],
    )
    return jsonify({"location": _location_response(row)}), 201


@app.delete("/weather/locations/<location_id>")
def remove_saved_location(location_id: str):
    try:
        parsed_id = UUID(location_id)
    except (ValueError, AttributeError):
        return _error("invalid_location_id", "location_id must be a UUID.", 400)
    try:
        result = delete_saved_location(parsed_id)
    except SavedLocationNotFoundError:
        return _error("location_not_found", "Saved location does not exist.", 404)
    except psycopg2.Error as error:
        logger.error(
            "Saved-location removal failed: category=database location_id=%s error_type=%s",
            parsed_id,
            type(error).__name__,
        )
        return _error(
            "database_unavailable",
            "The location and its weather data were not changed. Retry the request.",
            503,
        )
    logger.info(
        "Saved location removed: operation=delete location_id=%s documents_deleted=%s chunks_deleted=%s shared_documents_retained=%s",
        parsed_id,
        result["documents_deleted"],
        result["chunks_deleted"],
        result["shared_documents_retained"],
    )
    return jsonify(result)


@app.post("/weather/sync")
def sync_weather():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - {"locations", "limit"}:
        return _error("invalid_request", "Request body must be a JSON object.", 400)

    location_refs = body.get("locations")
    if not isinstance(location_refs, list) or not location_refs:
        return _error(
            "invalid_locations",
            "Provide at least one saved location.",
            400,
        )
    location_ids = []
    for reference in location_refs:
        if not isinstance(reference, dict) or set(reference) != {"id"}:
            return _error(
                "invalid_locations",
                "Each location must contain only its saved id.",
                400,
            )
        try:
            location_ids.append(UUID(reference["id"]))
        except (ValueError, TypeError, AttributeError):
            return _error("invalid_location_id", "Each location id must be a UUID.", 400)
    if len(set(location_ids)) != len(location_ids):
        return _error("duplicate_locations", "Do not submit a location more than once.", 400)

    limit = body.get("limit", 50)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return _error("invalid_limit", "limit must be a positive integer.", 400)

    try:
        rows = list_saved_locations()
    except psycopg2.Error as error:
        logger.error(
            "Weather sync failed: category=database operation=load_locations error_type=%s",
            type(error).__name__,
        )
        return _error("database_unavailable", "The location list is unavailable.", 503)
    saved_by_id = {row["id"]: row for row in rows}

    outcomes = []
    documents_synced = 0
    failure_kinds = []

    for location_id in location_ids:
        saved = saved_by_id.get(location_id)
        if saved is None:
            failure_kinds.append("validation")
            outcomes.append(
                {
                    "location_id": str(location_id),
                    "location": str(location_id),
                    "status": "failed",
                    "documents_synced": 0,
                    "request_budget_exhausted": False,
                    "error": "Saved location does not exist.",
                }
            )
            continue

        if documents_synced >= limit:
            outcomes.append(
                {
                    "location_id": str(location_id),
                    "location": saved["display_location"],
                    "status": "partial",
                    "documents_synced": 0,
                    "request_budget_exhausted": False,
                    "error": "The request document limit was reached before this location was processed.",
                }
            )
            continue

        try:
            result = get_weather_client().sync_location(
                saved["input_value"], location_id=str(location_id)
            )
        except LocationValidationError as error:
            failure_kinds.append("validation")
            logger.warning(
                "Weather sync failed: category=validation location_id=%s",
                location_id,
            )
            outcomes.append(
                {
                    "location_id": str(location_id),
                    "location": saved["display_location"],
                    "status": "failed",
                    "documents_synced": 0,
                    "request_budget_exhausted": False,
                    "error": str(error),
                }
            )
            continue
        except WeatherClientError as error:
            failure_kinds.append("weather")
            logger.warning(
                "Weather sync failed: category=provider location_id=%s",
                location_id,
            )
            outcomes.append(
                {
                    "location_id": str(location_id),
                    "location": saved["display_location"],
                    "status": "failed",
                    "documents_synced": 0,
                    "request_budget_exhausted": False,
                    "error": str(error),
                }
            )
            continue

        try:
            if result.resolved:
                update_saved_location_resolution(location_id, result.resolved)
            available = limit - documents_synced
            to_save = result.documents[:available]
            saved_count = upsert_weather_documents(to_save, location_id)
        except SavedLocationNotFoundError:
            failure_kinds.append("validation")
            outcomes.append(
                {
                    "location_id": str(location_id),
                    "location": saved["display_location"],
                    "status": "failed",
                    "documents_synced": 0,
                    "request_budget_exhausted": False,
                    "error": "Saved location was removed during synchronization.",
                }
            )
            continue
        except psycopg2.Error as error:
            logger.error(
                "Weather sync persistence failed: category=database location_id=%s error_type=%s",
                location_id,
                type(error).__name__,
            )
            return _error(
                "database_unavailable",
                "The database could not save synchronized weather. Retry the request.",
                503,
            )

        documents_synced += saved_count
        messages = list(result.warnings)
        if result.request_budget_exhausted:
            messages.append("The per-location weather request budget was exhausted.")
        if len(to_save) < len(result.documents):
            messages.append("The request document limit was reached.")
        status = "partial" if messages else "synced"
        outcomes.append(
            {
                "location_id": str(location_id),
                "location": saved["display_location"],
                "status": status,
                "documents_synced": saved_count,
                "request_budget_exhausted": result.request_budget_exhausted,
                "error": " ".join(messages) or None,
            }
        )
        logger.info(
            "Weather location sync completed: operation=sync location_id=%s status=%s documents=%d",
            location_id,
            status,
            saved_count,
        )
        if messages:
            failure_kinds.append("partial")

    if outcomes and all(item["status"] == "failed" for item in outcomes):
        status = 422 if failure_kinds and all(
            kind == "validation" for kind in failure_kinds
        ) else 502
        return jsonify({"documents_synced": documents_synced, "locations": outcomes}), status

    logger.info(
        "Weather sync completed: operation=sync locations=%d documents=%d",
        len(location_ids),
        documents_synced,
    )
    return jsonify({"documents_synced": documents_synced, "locations": outcomes})


@app.post("/weather/search")
def search_weather():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - {"query", "top_k"}:
        return _error("invalid_request", "Request body must be a JSON object.", 400)
    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        return _error("invalid_query", "query must be a non-empty string.", 400)

    top_k = body.get("top_k", 5)
    if isinstance(top_k, bool) or not isinstance(top_k, int):
        return _error("invalid_top_k", "top_k must be an integer.", 400)
    top_k = max(1, min(top_k, 20))

    try:
        query_vector = encode_query(query.strip())
    except (OSError, RuntimeError, ValueError) as error:
        logger.error(
            "Weather search failed: category=encoder error_type=%s",
            type(error).__name__,
        )
        return _error("encoder_unavailable", "The search encoder is unavailable.", 503)

    try:
        matches = search_weather_embeddings(
            query_vector,
            top_k,
            MODEL_NAME,
            MODEL_REVISION,
        )
    except psycopg2.Error as error:
        logger.error(
            "Weather search failed: category=database error_type=%s",
            type(error).__name__,
        )
        return _error("database_unavailable", "Weather search is unavailable.", 503)

    document_ids = sorted({row["document_id"] for row in matches})
    logger.info(
        "Weather search completed: operation=search matches=%d document_ids=%s",
        len(matches),
        ",".join(document_ids),
    )
    return jsonify({"matches": [_search_match_response(row) for row in matches]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
