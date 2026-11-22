BEGIN;

CREATE TABLE IF NOT EXISTS weather.weather_saved_locations (
    id UUID PRIMARY KEY,
    input_type TEXT NOT NULL
        CHECK (input_type IN ('city_state', 'coordinates')),
    input_value JSONB NOT NULL,
    display_location TEXT NOT NULL
        CHECK (length(trim(display_location)) > 0),
    latitude DOUBLE PRECISION
        CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION
        CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
    grid_id TEXT,
    grid_x INTEGER,
    grid_y INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((latitude IS NULL) = (longitude IS NULL))
);

CREATE TABLE IF NOT EXISTS weather.weather_location_documents (
    saved_location_id UUID NOT NULL
        REFERENCES weather.weather_saved_locations(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL
        REFERENCES weather.weather_documents(id) ON DELETE CASCADE,
    PRIMARY KEY (saved_location_id, document_id)
);

CREATE INDEX IF NOT EXISTS weather_location_documents_document_id_idx
    ON weather.weather_location_documents (document_id);

-- New rows no longer store a single owning location; alert grid fields are nullable.
ALTER TABLE weather.weather_documents
    ALTER COLUMN location DROP NOT NULL,
    ALTER COLUMN latitude DROP NOT NULL,
    ALTER COLUMN longitude DROP NOT NULL,
    ALTER COLUMN grid_id DROP NOT NULL,
    ALTER COLUMN grid_x DROP NOT NULL,
    ALTER COLUMN grid_y DROP NOT NULL;

CREATE TEMPORARY TABLE migration_saved_locations ON COMMIT DROP AS
SELECT DISTINCT
    (
        substr(key_hash, 1, 8) || '-' ||
        substr(key_hash, 9, 4) || '-' ||
        substr(key_hash, 13, 4) || '-' ||
        substr(key_hash, 17, 4) || '-' ||
        substr(key_hash, 21, 12)
    )::UUID AS id,
    location,
    latitude,
    longitude,
    grid_id,
    grid_x,
    grid_y
FROM (
    SELECT
        location,
        latitude,
        longitude,
        grid_id,
        grid_x,
        grid_y,
        md5(jsonb_build_array(
            location, latitude, longitude, grid_id, grid_x, grid_y
        )::TEXT) AS key_hash
    FROM weather.weather_documents
) AS legacy_locations;

INSERT INTO weather.weather_saved_locations (
    id, input_type, input_value, display_location, latitude, longitude,
    grid_id, grid_x, grid_y
)
SELECT
    id,
    'coordinates',
    jsonb_build_object('lat', latitude, 'lon', longitude),
    location,
    latitude,
    longitude,
    grid_id,
    grid_x,
    grid_y
FROM migration_saved_locations
ON CONFLICT (id) DO NOTHING;

INSERT INTO weather.weather_location_documents (saved_location_id, document_id)
SELECT locations.id, documents.id
FROM weather.weather_documents AS documents
JOIN migration_saved_locations AS locations
  ON documents.location IS NOT DISTINCT FROM locations.location
 AND documents.latitude IS NOT DISTINCT FROM locations.latitude
 AND documents.longitude IS NOT DISTINCT FROM locations.longitude
 AND documents.grid_id IS NOT DISTINCT FROM locations.grid_id
 AND documents.grid_x IS NOT DISTINCT FROM locations.grid_x
 AND documents.grid_y IS NOT DISTINCT FROM locations.grid_y
ON CONFLICT (saved_location_id, document_id) DO NOTHING;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM weather.weather_documents AS documents
        WHERE NOT EXISTS (
            SELECT 1
            FROM weather.weather_location_documents AS associations
            WHERE associations.document_id = documents.id
        )
    ) THEN
        RAISE EXCEPTION
            'Saved-location migration failed: one or more weather documents have no association';
    END IF;
END
$$;

COMMIT;
