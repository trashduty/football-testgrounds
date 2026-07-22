"use strict";


const elements = {
  form: document.getElementById("chart-filter-form"),
  season: document.getElementById("season"),
  weekStart: document.getElementById("week-start"),
  weekEnd: document.getElementById("week-end"),
  conference: document.getElementById("conference"),
  minimumPlays: document.getElementById("minimum-plays"),

  excludeGarbageTime: document.getElementById(
    "exclude-garbage-time"
  ),

  redZoneOnly: document.getElementById(
    "red-zone-only"
  ),

  goalToGoOnly: document.getElementById(
    "goal-to-go-only"
  ),

  resetButton: document.getElementById("reset-filters"),
  generateButton: document.getElementById("generate-chart"),
  downloadButton: document.getElementById("download-chart"),

  chartImage: document.getElementById("chart-image"),
  chartPlaceholder: document.getElementById("chart-placeholder"),
  chartLoading: document.getElementById("chart-loading"),
  chartStatus: document.getElementById("chart-status"),
  chartSummary: document.getElementById("chart-summary"),
  errorMessage: document.getElementById("error-message"),
};


let currentChartUrl = null;


function getCheckedValues(name) {
  return Array.from(
    document.querySelectorAll(
      `input[name="${name}"]:checked`
    )
  ).map((input) => input.value);
}


function getSelectedPlayType() {
  const selected = document.querySelector(
    'input[name="play_type"]:checked'
  );

  return selected ? selected.value : "all";
}


function setStatus(text, state = "ready") {
  elements.chartStatus.textContent = text;
  elements.chartStatus.dataset.state = state;
}


function showError(message) {
  elements.errorMessage.textContent = message;
  elements.errorMessage.hidden = false;

  setStatus("Error", "error");
}


function clearError() {
  elements.errorMessage.textContent = "";
  elements.errorMessage.hidden = true;
}


function setLoading(isLoading) {
  elements.generateButton.disabled = isLoading;

  elements.generateButton.textContent = (
    isLoading
      ? "Generating…"
      : "Generate chart"
  );

  elements.chartLoading.hidden = !isLoading;

  if (isLoading) {
    elements.chartImage.hidden = true;
    elements.chartPlaceholder.hidden = true;
    elements.downloadButton.disabled = true;

    setStatus("Rendering", "loading");
  }
}


function validateFilters() {
  const weekStart = Number(elements.weekStart.value);
  const weekEnd = Number(elements.weekEnd.value);
  const minimumPlays = Number(elements.minimumPlays.value);

  const downs = getCheckedValues("downs");
  const periods = getCheckedValues("periods");

  if (!elements.season.value) {
    throw new Error("Select a season.");
  }

  if (!Number.isFinite(weekStart) || !Number.isFinite(weekEnd)) {
    throw new Error("Enter valid starting and ending weeks.");
  }

  if (weekStart > weekEnd) {
    throw new Error(
      "The starting week cannot be greater than the ending week."
    );
  }

  if (downs.length === 0) {
    throw new Error("Select at least one down.");
  }

  if (periods.length === 0) {
    throw new Error("Select at least one quarter or overtime.");
  }

  if (!Number.isFinite(minimumPlays) || minimumPlays < 1) {
    throw new Error(
      "Minimum plays must be at least 1."
    );
  }
}


function buildChartParameters({ download = false } = {}) {
  validateFilters();

  const parameters = new URLSearchParams();

  parameters.set(
    "season",
    elements.season.value
  );

  parameters.set(
    "week_start",
    elements.weekStart.value
  );

  parameters.set(
    "week_end",
    elements.weekEnd.value
  );

  parameters.set(
    "play_type",
    getSelectedPlayType()
  );

  parameters.set(
    "downs",
    getCheckedValues("downs").join(",")
  );

  parameters.set(
    "periods",
    getCheckedValues("periods").join(",")
  );

  parameters.set(
    "exclude_garbage_time",
    String(elements.excludeGarbageTime.checked)
  );

  parameters.set(
    "minimum_plays",
    elements.minimumPlays.value
  );

  parameters.set(
    "red_zone_only",
    String(elements.redZoneOnly.checked)
  );

  parameters.set(
    "goal_to_go_only",
    String(elements.goalToGoOnly.checked)
  );

  if (elements.conference.value) {
    parameters.set(
      "conference",
      elements.conference.value
    );
  }

  parameters.set("width", "1600");
  parameters.set("height", "1000");
  parameters.set("scale", "1");

  if (download) {
    parameters.set("download", "true");
  }

  // Prevent the browser from displaying a previously cached image.
  parameters.set(
    "_",
    String(Date.now())
  );

  return parameters;
}


function buildChartUrl({ download = false } = {}) {
  const parameters = buildChartParameters({ download });

  return (
    "/api/charts/team-tiers.png?"
    + parameters.toString()
  );
}


function createFilterSummary() {
  const season = elements.season.value;

  const weekStart = elements.weekStart.value;
  const weekEnd = elements.weekEnd.value;

  const weekText = (
    weekStart === weekEnd
      ? `Week ${weekStart}`
      : `Weeks ${weekStart}–${weekEnd}`
  );

  const playType = getSelectedPlayType();

  const playText = (
    playType === "all"
      ? "all plays"
      : `${playType} plays`
  );

  const conference = (
    elements.conference.value
      ? elements.conference.value
      : "all conferences"
  );

  return (
    `${season} | ${weekText} | ${playText} | `
    + `${conference} | minimum `
    + `${elements.minimumPlays.value} plays`
  );
}


async function extractApiError(response) {
  try {
    const payload = await response.json();

    if (payload && payload.detail) {
      return String(payload.detail);
    }
  } catch (error) {
    // The response might not be JSON.
  }

  return (
    `The chart request failed with status `
    + `${response.status}.`
  );
}


async function generateChart() {
  clearError();
  setLoading(true);

  try {
    const chartUrl = buildChartUrl();

    const response = await fetch(
      chartUrl,
      {
        method: "GET",
        cache: "no-store",
      }
    );

    if (!response.ok) {
      const message = await extractApiError(response);
      throw new Error(message);
    }

    const imageBlob = await response.blob();

    if (!imageBlob.type.startsWith("image/")) {
      throw new Error(
        "The server response was not an image."
      );
    }

    if (currentChartUrl) {
      URL.revokeObjectURL(currentChartUrl);
    }

    currentChartUrl = URL.createObjectURL(imageBlob);

    elements.chartImage.src = currentChartUrl;
    elements.chartImage.hidden = false;
    elements.chartPlaceholder.hidden = true;

    elements.chartSummary.textContent = createFilterSummary();

    elements.downloadButton.disabled = false;

    setStatus("Complete", "success");

  } catch (error) {
    elements.chartImage.hidden = true;
    elements.chartPlaceholder.hidden = false;

    showError(
      error instanceof Error
        ? error.message
        : "The chart could not be generated."
    );

  } finally {
    setLoading(false);
  }
}


function downloadChart() {
  try {
    const downloadUrl = buildChartUrl({
      download: true,
    });

    const link = document.createElement("a");

    link.href = downloadUrl;
    link.download = "";

    document.body.appendChild(link);
    link.click();
    link.remove();

  } catch (error) {
    showError(
      error instanceof Error
        ? error.message
        : "The chart could not be downloaded."
    );
  }
}


function setCheckboxGroup(name, values) {
  const allowed = new Set(
    values.map(String)
  );

  document.querySelectorAll(
    `input[name="${name}"]`
  ).forEach((input) => {
    input.checked = allowed.has(input.value);
  });
}


function resetFilters() {
  clearError();

  const firstSeasonOption = (
    elements.season.options[0]
  );

  if (firstSeasonOption) {
    elements.season.value = firstSeasonOption.value;
  }

  elements.weekStart.value = "1";
  elements.weekEnd.value = "20";
  elements.conference.value = "";
  elements.minimumPlays.value = "100";

  const allPlayInput = document.querySelector(
    'input[name="play_type"][value="all"]'
  );

  if (allPlayInput) {
    allPlayInput.checked = true;
  }

  setCheckboxGroup(
    "downs",
    ["1", "2", "3", "4"]
  );

  setCheckboxGroup(
    "periods",
    ["1", "2", "3", "4"]
  );

  elements.excludeGarbageTime.checked = true;
  elements.redZoneOnly.checked = false;
  elements.goalToGoOnly.checked = false;

  elements.chartSummary.textContent = (
    "Select filters and generate the chart."
  );

  elements.downloadButton.disabled = true;
  elements.chartImage.hidden = true;
  elements.chartPlaceholder.hidden = false;

  setStatus("Ready", "ready");
}


function populateSeasons(seasons) {
  elements.season.innerHTML = "";

  if (!Array.isArray(seasons) || seasons.length === 0) {
    const option = document.createElement("option");

    option.value = "";
    option.textContent = "No seasons available";

    elements.season.appendChild(option);
    return;
  }

  seasons.forEach((season, index) => {
    const option = document.createElement("option");

    option.value = String(season);
    option.textContent = String(season);

    if (index === 0) {
      option.selected = true;
    }

    elements.season.appendChild(option);
  });
}


function populateConferences(conferences) {
  elements.conference.innerHTML = "";

  const allOption = document.createElement("option");

  allOption.value = "";
  allOption.textContent = "All conferences";

  elements.conference.appendChild(allOption);

  if (!Array.isArray(conferences)) {
    return;
  }

  conferences.forEach((conference) => {
    const option = document.createElement("option");

    option.value = String(conference);
    option.textContent = String(conference);

    elements.conference.appendChild(option);
  });
}


async function loadMetadata() {
  setStatus("Loading", "loading");

  try {
    const response = await fetch(
      "/api/charts/metadata",
      {
        cache: "no-store",
      }
    );

    if (!response.ok) {
      const message = await extractApiError(response);
      throw new Error(message);
    }

    const metadata = await response.json();

    populateSeasons(metadata.seasons);
    populateConferences(metadata.conferences);

    setStatus("Ready", "ready");

    // Generate the default chart automatically.
    await generateChart();

  } catch (error) {
    showError(
      error instanceof Error
        ? error.message
        : "Chart metadata could not be loaded."
    );
  }
}


elements.form.addEventListener(
  "submit",
  (event) => {
    event.preventDefault();
    generateChart();
  }
);


elements.downloadButton.addEventListener(
  "click",
  downloadChart
);


elements.resetButton.addEventListener(
  "click",
  resetFilters
);


window.addEventListener(
  "beforeunload",
  () => {
    if (currentChartUrl) {
      URL.revokeObjectURL(currentChartUrl);
    }
  }
);


loadMetadata();
