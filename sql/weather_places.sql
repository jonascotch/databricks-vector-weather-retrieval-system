BEGIN;

-- Reference table for the city autocomplete list. Populate it with
-- notebooks/ingest_weather_places.py, which downloads the pinned Census
-- Gazetteer release and refreshes the table idempotently. The table-level
-- grants in specs/001-weather-vector-search/deployment.md cover this table
-- because they apply to all tables in the weather schema.

CREATE TABLE IF NOT EXISTS weather.weather_places (
    geoid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_normalized TEXT NOT NULL,
    state TEXT NOT NULL
        CHECK (state ~ '^[A-Z]{2}$'),
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    CONSTRAINT weather_places_name_state_key
        UNIQUE (name_normalized, state)
);

CREATE INDEX IF NOT EXISTS weather_places_name_prefix_idx
    ON weather.weather_places (name_normalized text_pattern_ops);

COMMIT;
