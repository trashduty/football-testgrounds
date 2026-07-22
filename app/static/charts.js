"use strict";


const elements = {
  form: document.getElementById("chart-filter-form"),

  season: document.getElementById("season"),
  weekStart: document.getElementById("week-start"),
  weekEnd: document.getElementById("week-end"),
  conference: document.getElementById("conference"),
  chartMetric: document.getElementById("chart-metric"),
  logoSize: document.getElementById("logo-size"),
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

  teamSearch: document.getElementById("team-search"),
  teamOptions: document.getElementById("team-options"),
  selectedTeams: document.getElementById("selected-teams"),
  addTeamButton: document.getElementById("add-team"),
  clearTeamsButton: document.getElementById("clear-teams"),

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
let availableTeams = [];
let selectedTeams = [];


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


function normalizeTeamName(value) {
  const normalized = value.trim().toLowerCase();

  return availableTeams.find(
    (team) => team.toLowerCase() === normalized
  ) || null;
}


function renderSelectedTeams() {
  elements.selectedTeams.innerHTML = "";

  if (selectedTeams.length === 0) {
    const empty = document.createElement("span");

    empty.className = "empty-team-message";
    empty.textContent = "No teams selected";

    elements.selectedTeams.appendChild(empty);
    return;
  }

  selectedTeams.forEach((team) => {
    const chip = document.createElement("span");

    chip.className = "team-chip";

    const label = document.createElement("span");
    label.textContent = team;

    const removeButton = document.createElement("button");

    removeButton.type = "button";
    removeButton.setAttribute(
      "aria-label",
      `Remove ${team}`
    );

    removeButton.textContent = "×";

    removeButton.addEventListener(
      "click",
      () => {
        selectedTeams = selectedTeams.filter(
          (selectedTeam) => selectedTeam !== team
        );

        renderSelectedTeams();
      }
    );

    chip.appendChild(label);
    chip.appendChild(removeButton);

    elements.selectedTeams.appendChild(chip);
  });
}


function addTeam() {
  clearError();

  const matchedTeam = normalizeTeamName(
    elements.teamSearch.value
  );

  if (!matchedTeam) {
    showError(
      "Select a valid FBS team from the search suggestions."
    );

    return;
  }

  if (!selectedTeams.includes(matchedTeam)) {
    selectedTeams.push(matchedTeam);
  }

  elements.teamSearch.value = "";

  renderSelectedTeams();
}


function validateFilters() {
  const weekStart = Number(elements.weekStart.value);
  const weekEnd = Number(elements.weekEnd.value);
  const minimumPlays = Number(elements.minimumPlays.value);

  if (!elements.season.value) {
    throw new Error("Select a season.");
  }

  if (weekStart > weekEnd) {
    throw new Error(
      "The starting week cannot be greater than the ending week."
    );
  }

  if (getCheckedValues("downs").length === 0) {
    throw new Error("Select at least one down.");
  }

  if (getCheckedValues("periods").length === 0) {
    throw new Error(
      "Select at least one quarter or overtime."
    );
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
    "metric",
    elements.chartMetric.value
  );

  parameters.set(
    "logo_size",
    elements.logoSize.value
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

  if (selectedTeams.length > 0) {
    parameters.set(
      "teams",
      selectedTeams.join(",")
    );
  } else if (elements.conference.value) {
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

  parameters.set(
    "_",
    String(Date.now())
  );

  return parameters;
}


function buildChartUrl({ download = false } = {}) {
  return (
    "/api/charts/team-tiers.png?"
    + buildChartParameters({ download }).toString()
  );
}


function createFilterSummary() {
  const metricLabel = (
    elements.chartMetric
      .options[elements.chartMetric.selectedIndex]
      .textContent.trim()
  );

  const displayText = (
    selectedTeams.length > 0
      ? selectedTeams.join(" vs. ")
      : (
          elements.conference.value
            ? `${elements.conference.value} teams`
            : "All FBS teams"
        )
  );

  return (
    `${metricLabel} | ${displayText} | `
    + `all-FBS axes and averages`
  );
}


async function extractApiError(response) {
  try {
    const payload = await response.json();

    if (payload && payload.detail) {
      return String(payload.detail);
    }
  } catch (error) {
    // The response might not contain JSON.
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
    const response = await fetch(
      buildChartUrl(),
      {
        cache: "no-store",
      }
    );

    if (!response.ok) {
      throw new Error(
        await extractApiError(response)
      );
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

    currentChartUrl = URL.createObjectURL(
      imageBlob
    );

    elements.chartImage.src = currentChartUrl;
    elements.chartImage.hidden = false;
    elements.chartPlaceholder.hidden = true;

    elements.chartSummary.textContent = (
      createFilterSummary()
    );

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
    const link = document.createElement("a");

    link.href = buildChartUrl({
      download: true,
    });

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


function populateOptions(
  select,
  values,
  allLabel = null,
) {
  select.innerHTML = "";

  if (allLabel !== null) {
    const allOption = document.createElement("option");

    allOption.value = "";
    allOption.textContent = allLabel;

    select.appendChild(allOption);
  }

  values.forEach((value, index) => {
    const option = document.createElement("option");

    option.value = String(value);
    option.textContent = String(value);

    if (allLabel === null && index === 0) {
      option.selected = true;
    }

    select.appendChild(option);
  });
}


function populateTeamSearch(teams) {
  availableTeams = Array.isArray(teams)
    ? teams
    : [];

  elements.teamOptions.innerHTML = "";

  availableTeams.forEach((team) => {
    const option = document.createElement("option");

    option.value = team;

    elements.teamOptions.appendChild(option);
  });
}


async function loadMetadata() {
  setStatus("Loading", "loading");

  try {
    const [metadataResponse, teamsResponse] = await Promise.all([
      fetch(
        "/api/charts/metadata",
        { cache: "no-store" }
      ),
      fetch(
        "/api/charts/teams",
        { cache: "no-store" }
      ),
    ]);

    if (!metadataResponse.ok) {
      throw new Error(
        await extractApiError(metadataResponse)
      );
    }

    if (!teamsResponse.ok) {
      throw new Error(
        await extractApiError(teamsResponse)
      );
    }

    const metadata = await metadataResponse.json();
    const teamPayload = await teamsResponse.json();

    populateOptions(
      elements.season,
      metadata.seasons
    );

    populateOptions(
      elements.conference,
      metadata.conferences,
      "All FBS conferences"
    );

    populateTeamSearch(
      teamPayload.teams
    );

    setStatus("Ready", "ready");

    await generateChart();

  } catch (error) {
    showError(
      error instanceof Error
        ? error.message
        : "Dashboard metadata could not be loaded."
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


elements.addTeamButton.addEventListener(
  "click",
  addTeam
);


elements.teamSearch.addEventListener(
  "keydown",
  (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addTeam();
    }
  }
);


elements.clearTeamsButton.addEventListener(
  "click",
  () => {
    selectedTeams = [];
    renderSelectedTeams();
  }
);


elements.downloadButton.addEventListener(
  "click",
  downloadChart
);


elements.resetButton.addEventListener(
  "click",
  () => {
    selectedTeams = [];
    renderSelectedTeams();

    elements.weekStart.value = "1";
    elements.weekEnd.value = "20";
    elements.minimumPlays.value = "100";
    elements.conference.value = "";
    elements.chartMetric.value = "epa";
    elements.logoSize.value = "auto";

    elements.excludeGarbageTime.checked = true;
    elements.redZoneOnly.checked = false;
    elements.goalToGoOnly.checked = false;

    elements.downloadButton.disabled = true;
    elements.chartImage.hidden = true;
    elements.chartPlaceholder.hidden = false;

    setStatus("Ready", "ready");
  }
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
