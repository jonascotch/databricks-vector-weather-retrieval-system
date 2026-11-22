(() => {
  const locationEntryForm = document.querySelector("#location-entry-form");
  const modeInputs = [...document.querySelectorAll('input[name="location-mode"]')];
  const cityFields = document.querySelector("#city-state-fields");
  const coordinateFields = document.querySelector("#coordinate-fields");
  const cityInput = document.querySelector("#city-state");
  const suggestionsList = document.querySelector("#place-suggestions");
  const latitudeInput = document.querySelector("#latitude");
  const longitudeInput = document.querySelector("#longitude");
  const locationError = document.querySelector("#location-error");
  const locationStatus = document.querySelector("#location-status");
  const emptyLocations = document.querySelector("#empty-locations");
  const selectedLocationsList = document.querySelector("#selected-locations");
  const addButton = document.querySelector("#add-location");
  const syncButton = document.querySelector("#sync-button");
  const syncStatus = document.querySelector("#sync-status");
  const syncResults = document.querySelector("#sync-results");
  const searchForm = document.querySelector("#search-form");
  const searchQuery = document.querySelector("#search-query");
  const searchButton = document.querySelector("#search-button");
  const searchStatus = document.querySelector("#search-status");
  const searchResults = document.querySelector("#search-results");
  const selectedLocations = [];
  let busy = false;
  let searching = false;
  let selectedPlace = null;
  let currentSuggestions = [];
  let activeSuggestion = -1;
  let suggestionTimer = 0;
  let suggestionSequence = 0;

  function currentMode() {
    return modeInputs.find((input) => input.checked).value;
  }

  function updateLocationMode() {
    const cityMode = currentMode() === "city-state";
    cityFields.hidden = !cityMode;
    coordinateFields.hidden = cityMode;
    cityInput.required = cityMode;
    latitudeInput.required = !cityMode;
    longitudeInput.required = !cityMode;
  }

  function updateControls() {
    for (const input of modeInputs) {
      input.disabled = busy;
    }
    cityInput.disabled = busy;
    latitudeInput.disabled = busy;
    longitudeInput.disabled = busy;
    addButton.disabled = busy;
    syncButton.disabled = busy || selectedLocations.length === 0;
    selectedLocationsList
      .querySelectorAll(".remove-location")
      .forEach((button) => {
        button.disabled = busy;
      });
  }

  async function requestJson(url, options, allowLocationOutcomes = false) {
    let response;
    try {
      response = await fetch(url, options);
    } catch {
      throw new Error("The server could not be reached. Check the connection and retry.");
    }
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error("The server returned an invalid response.");
    }
    if (!response.ok && !(allowLocationOutcomes && Array.isArray(data.locations))) {
      throw new Error(data.error?.message || "The request failed.");
    }
    return data;
  }

  function hideSuggestions() {
    suggestionsList.replaceChildren();
    suggestionsList.hidden = true;
    currentSuggestions = [];
    activeSuggestion = -1;
  }

  function renderSuggestions(places) {
    currentSuggestions = places;
    activeSuggestion = -1;
    suggestionsList.replaceChildren();
    if (!places.length) {
      suggestionsList.hidden = true;
      return;
    }
    for (const place of places) {
      const item = document.createElement("li");
      item.textContent = `${place.name}, ${place.state}`;
      item.setAttribute("role", "option");
      item.addEventListener("mousedown", (event) => {
        event.preventDefault();
        selectPlace(place);
      });
      suggestionsList.append(item);
    }
    suggestionsList.hidden = false;
  }

  function selectPlace(place) {
    selectedPlace = place;
    cityInput.value = `${place.name}, ${place.state}`;
    hideSuggestions();
  }

  async function loadSuggestions() {
    const query = cityInput.value.trim();
    const normalized = query.replace(/\s+/g, " ").toLowerCase();
    if (normalized.length < 2) {
      hideSuggestions();
      return;
    }
    const sequence = ++suggestionSequence;
    try {
      const data = await requestJson(
        `/weather/places?q=${encodeURIComponent(query)}`,
      );
      if (
        sequence === suggestionSequence &&
        currentMode() === "city-state" &&
        !selectedPlace
      ) {
        renderSuggestions(data.places || []);
      }
    } catch {
      if (sequence === suggestionSequence) {
        hideSuggestions();
      }
    }
  }

  function scheduleSuggestions() {
    selectedPlace = null;
    window.clearTimeout(suggestionTimer);
    suggestionTimer = window.setTimeout(loadSuggestions, 200);
  }

  function moveActiveSuggestion(offset) {
    if (!currentSuggestions.length) {
      return;
    }
    activeSuggestion =
      (activeSuggestion + offset + currentSuggestions.length) %
      currentSuggestions.length;
    [...suggestionsList.querySelectorAll("li")].forEach((item, index) => {
      item.classList.toggle("active", index === activeSuggestion);
    });
  }

  function chooseActiveSuggestion() {
    if (!currentSuggestions.length) {
      return;
    }
    const index = activeSuggestion >= 0 ? activeSuggestion : 0;
    selectPlace(currentSuggestions[index]);
  }

  function handleSuggestionKeys(event) {
    if (suggestionsList.hidden) {
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveActiveSuggestion(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      moveActiveSuggestion(-1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      chooseActiveSuggestion();
    } else if (event.key === "Escape") {
      hideSuggestions();
    }
  }

  function renderSelectedLocations() {
    selectedLocationsList.replaceChildren();
    emptyLocations.hidden = selectedLocations.length > 0;

    for (const location of selectedLocations) {
      const item = document.createElement("li");
      item.className = "selected-location";

      const label = document.createElement("span");
      label.textContent = location.display_location;

      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.className = "remove-location";
      removeButton.textContent = "Remove";
      removeButton.setAttribute(
        "aria-label",
        `Remove ${location.display_location}`,
      );
      removeButton.disabled = busy;
      removeButton.addEventListener("click", () => removeLocation(location));

      item.append(label, removeButton);
      selectedLocationsList.append(item);
    }

    updateControls();
  }

  async function loadLocations() {
    busy = true;
    updateControls();
    locationStatus.textContent = "Loading saved locations…";
    try {
      const data = await requestJson("/weather/locations");
      selectedLocations.splice(0, selectedLocations.length, ...data.locations);
      locationStatus.textContent = "";
      renderSelectedLocations();
    } catch (error) {
      locationError.textContent = error.message;
      locationStatus.textContent = "";
    } finally {
      busy = false;
      renderSelectedLocations();
    }
  }

  function getLocationInput() {
    if (currentMode() === "city-state") {
      if (selectedPlace) {
        return {
          name: selectedPlace.name,
          state: selectedPlace.state,
          lat: selectedPlace.latitude,
          lon: selectedPlace.longitude,
        };
      }
      throw new Error(
        "Start typing a city and choose a suggested place before adding it.",
      );
    }

    const latitude = latitudeInput.value.trim();
    const longitude = longitudeInput.value.trim();
    if (latitude === "" || longitude === "") {
      throw new Error("Enter both latitude and longitude.");
    }
    const lat = Number(latitude);
    const lon = Number(longitude);
    if (!Number.isFinite(lat) || lat < -90 || lat > 90) {
      throw new Error("Latitude must be between -90 and 90.");
    }
    if (!Number.isFinite(lon) || lon < -180 || lon > 180) {
      throw new Error("Longitude must be between -180 and 180.");
    }
    return { lat, lon };
  }

  async function addLocation(event) {
    event.preventDefault();
    locationError.textContent = "";
    locationStatus.textContent = "";
    let location;
    try {
      location = getLocationInput();
    } catch (error) {
      locationError.textContent = error.message;
      return;
    }

    busy = true;
    updateControls();
    locationStatus.textContent = "Saving location…";
    try {
      const data = await requestJson("/weather/locations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ location }),
      });
      selectedLocations.push(data.location);
      locationEntryForm.reset();
      selectedPlace = null;
      hideSuggestions();
      updateLocationMode();
      syncResults.replaceChildren();
      syncStatus.textContent = "";
      locationStatus.textContent = `${data.location.display_location} was saved.`;
      renderSelectedLocations();
    } catch (error) {
      locationError.textContent = error.message;
      locationStatus.textContent = "";
    } finally {
      busy = false;
      renderSelectedLocations();
    }
  }

  async function removeLocation(location) {
    locationError.textContent = "";
    locationStatus.textContent = "";
    busy = true;
    updateControls();
    locationStatus.textContent = "Removing location and unshared weather data…";
    try {
      const result = await requestJson(
        `/weather/locations/${encodeURIComponent(location.id)}`,
        { method: "DELETE" },
      );
      const index = selectedLocations.findIndex(
        (saved) => saved.id === location.id,
      );
      if (index >= 0) {
        selectedLocations.splice(index, 1);
      }
      locationStatus.textContent =
        `Removed ${location.display_location}: ${result.documents_deleted} documents and ` +
        `${result.chunks_deleted} chunks deleted; ` +
        `${result.shared_documents_retained} shared documents retained.`;
      renderSelectedLocations();
    } catch (error) {
      locationError.textContent = error.message;
      locationStatus.textContent = "";
    } finally {
      busy = false;
      renderSelectedLocations();
    }
  }

  function appendResult(locationResult) {
    const card = document.createElement("article");
    card.className = `result-card result-${locationResult.status}`;
    const heading = document.createElement("h3");
    heading.textContent = locationResult.location;
    const detail = document.createElement("p");
    detail.textContent =
      `${locationResult.status}: ${locationResult.documents_synced} documents synchronized`;
    card.append(heading, detail);

    if (locationResult.request_budget_exhausted) {
      const budget = document.createElement("p");
      budget.textContent = "The per-location request budget was exhausted.";
      card.append(budget);
    }
    if (locationResult.error) {
      const error = document.createElement("p");
      error.textContent = locationResult.error;
      card.append(error);
    }
    syncResults.append(card);
  }

  function formatTimestamp(value) {
    if (!value) {
      return "Unavailable";
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Unavailable" : date.toLocaleString();
  }

  function appendSearchMatch(match) {
    const card = document.createElement("article");
    card.className = "search-match";
    const heading = document.createElement("h3");
    heading.textContent = match.headline || match.source_type;
    const source = document.createElement("p");
    source.textContent =
      `${match.source_type} · similarity ${match.similarity.toFixed(3)}`;
    const chunk = document.createElement("p");
    chunk.className = "match-text";
    chunk.textContent = match.chunk_text;
    const locationsHeading = document.createElement("h4");
    locationsHeading.textContent = "Associated locations";
    const locations = document.createElement("ul");
    for (const label of match.locations) {
      const item = document.createElement("li");
      item.textContent = label;
      locations.append(item);
    }
    const metadata = document.createElement("dl");
    for (const [label, value] of [
      ["Synchronized", match.synced_at],
      ["Issued", match.issued_at],
      ["Effective", match.effective_at],
      ["Forecast period starts", match.period_start],
      ["Forecast period ends", match.period_end],
    ]) {
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = formatTimestamp(value);
      metadata.append(term, detail);
    }
    card.append(heading, source, chunk, locationsHeading, locations, metadata);
    searchResults.append(card);
  }

  async function searchWeather(event) {
    event.preventDefault();
    const query = searchQuery.value.trim();
    searchStatus.textContent = "";
    searchResults.replaceChildren();
    if (!query) {
      searchStatus.textContent = "Enter a search query.";
      searchQuery.focus();
      return;
    }

    searching = true;
    searchButton.disabled = true;
    searchStatus.textContent = "Searching weather documents…";
    try {
      const data = await requestJson("/weather/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      if (data.matches.length === 0) {
        searchStatus.textContent =
          "No searchable matches yet. Synchronize weather first; new documents become searchable once embedding completes.";
        return;
      }
      for (const match of data.matches) {
        appendSearchMatch(match);
      }
      searchStatus.textContent = `${data.matches.length} matches found.`;
    } catch (error) {
      searchStatus.textContent = error.message;
    } finally {
      searching = false;
      searchButton.disabled = false;
    }
  }

  async function syncWeather() {
    syncStatus.textContent = "";
    syncResults.replaceChildren();
    if (selectedLocations.length === 0) {
      syncStatus.textContent = "Add at least one location before synchronizing.";
      return;
    }

    busy = true;
    updateControls();
    syncStatus.textContent = "Synchronizing saved locations…";
    try {
      const data = await requestJson(
        "/weather/sync",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            locations: selectedLocations.map((location) => ({ id: location.id })),
          }),
        },
        true,
      );
      for (const result of data.locations || []) {
        appendResult(result);
      }
      let syncMessage = `${data.documents_synced} documents synchronized.`;
      if (data.embedding?.status === "completed") {
        syncMessage += ` ${data.embedding.documents_embedded} documents embedded and searchable.`;
      } else if (data.embedding?.status === "failed") {
        syncMessage +=
          " Embedding did not complete; run the embedding notebook to make the latest documents searchable.";
      }
      syncStatus.textContent = syncMessage;
    } catch (error) {
      syncStatus.textContent = error.message;
    } finally {
      busy = false;
      updateControls();
    }
  }

  for (const input of modeInputs) {
    input.addEventListener("change", () => {
      updateLocationMode();
      hideSuggestions();
      locationError.textContent = "";
    });
  }
  for (const input of [cityInput, latitudeInput, longitudeInput]) {
    input.addEventListener("input", () => {
      locationError.textContent = "";
    });
  }
  cityInput.addEventListener("input", scheduleSuggestions);
  cityInput.addEventListener("keydown", handleSuggestionKeys);
  cityInput.addEventListener("blur", () => {
    window.setTimeout(hideSuggestions, 150);
  });
  locationEntryForm.addEventListener("submit", addLocation);
  syncButton.addEventListener("click", syncWeather);
  searchForm.addEventListener("submit", searchWeather);

  updateLocationMode();
  renderSelectedLocations();
  loadLocations();
})();
