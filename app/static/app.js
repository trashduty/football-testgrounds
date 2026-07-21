"use strict";

const searchForm = document.getElementById("search-form");
const searchInput = document.getElementById("search-input");
const searchButton = document.getElementById("search-button");

const loadingMessage = document.getElementById("loading-message");
const errorMessage = document.getElementById("error-message");
const resultsSection = document.getElementById("results-section");

const answerText = document.getElementById("answer-text");
const resultSeason = document.getElementById("result-season");

const teamValue = document.getElementById("team-value");
const metricName = document.getElementById("metric-name");
const teamRank = document.getElementById("team-rank");
const teamsRanked = document.getElementById("teams-ranked");
const leagueAverage = document.getElementById("league-average");
const differenceValue = document.getElementById("difference-value");
const percentileValue = document.getElementById("percentile-value");
const sampleSize = document.getElementById("sample-size");
const rawResponse = document.getElementById("raw-response");

const exampleButtons = document.querySelectorAll(".example-button");

const metricLabels = {
  off_epa_per_play: "EPA per play",
  off_epa_per_rush: "EPA per rush",
  off_epa_per_pass: "EPA per pass",
  off_success_rate: "Success rate",
  off_rush_success_rate: "Rush success rate",
  off_pass_success_rate: "Pass success rate",
  def_epa_allowed_per_play: "Defensive EPA allowed per play",
  def_epa_allowed_per_rush: "Defensive EPA allowed per rush",
  def_epa_allowed_per_pass: "Defensive EPA allowed per pass",
  def_success_rate_allowed: "Success rate allowed",
  def_rush_success_rate_allowed: "Rush success rate allowed",
  def_pass_success_rate_allowed: "Pass success rate allowed",
};

function isRateMetric(metric) {
  return metric.includes("success_rate");
}

function formatMetricValue(value, metric, includeSign = false) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  const numericValue = Number(value);

  if (isRateMetric(metric)) {
    return numericValue.toLocaleString(undefined, {
      style: "percent",
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
      signDisplay: includeSign ? "always" : "auto",
    });
  }

  return numericValue.toLocaleString(undefined, {
    minimumFractionDigits: 3,
    maximumFractionDigits: 3,
    signDisplay: includeSign ? "always" : "auto",
  });
}

function formatPercentile(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  const numericValue = Number(value);

  /*
   * This supports either:
   * 0.91 meaning the 91st percentile, or
   * 91 meaning the 91st percentile.
   */
  const normalizedValue = numericValue <= 1
    ? numericValue * 100
    : numericValue;

  return `${normalizedValue.toFixed(1)}th`;
}

function setLoading(isLoading) {
  loadingMessage.classList.toggle("hidden", !isLoading);
  searchButton.disabled = isLoading;
  searchInput.disabled = isLoading;

  searchButton.textContent = isLoading
    ? "Searching…"
    : "Search";
}

function clearError() {
  errorMessage.textContent = "";
  errorMessage.classList.add("hidden");
}

function showError(message) {
  resultsSection.classList.add("hidden");
  errorMessage.textContent = message;
  errorMessage.classList.remove("hidden");
}

function renderResult(payload) {
  const result = payload.result;

  if (!result) {
    throw new Error("The server returned no statistics result.");
  }

  const metric = result.metric;
  const label = metricLabels[metric] ?? metric.replaceAll("_", " ");

  answerText.textContent = payload.answer ?? "Statistic found.";
  resultSeason.textContent = result.season;

  teamValue.textContent = formatMetricValue(
    result.value,
    metric
  );

  metricName.textContent = `${result.team} ${label}`;

  teamRank.textContent = result.rank ?? "—";
  teamsRanked.textContent = result.teams_ranked
    ? `of ${result.teams_ranked} teams`
    : "";

  leagueAverage.textContent = formatMetricValue(
    result.league_average,
    metric
  );

  differenceValue.textContent = formatMetricValue(
    result.difference_from_average,
    metric,
    true
  );

  percentileValue.textContent = formatPercentile(
    result.percentile
  );

  sampleSize.textContent = Number.isFinite(Number(result.sample_size))
    ? Number(result.sample_size).toLocaleString()
    : "—";

  rawResponse.textContent = JSON.stringify(payload, null, 2);

  resultsSection.classList.remove("hidden");
  resultsSection.scrollIntoView({
    behavior: "smooth",
    block: "start",
  });
}

async function runSearch(query) {
  const trimmedQuery = query.trim();

  if (trimmedQuery.length < 3) {
    showError("Enter a longer statistics question.");
    return;
  }

  clearError();
  resultsSection.classList.add("hidden");
  setLoading(true);

  try {
    const response = await fetch(
      `/api/search?q=${encodeURIComponent(trimmedQuery)}`,
      {
        headers: {
          Accept: "application/json",
        },
      }
    );

    let payload;

    try {
      payload = await response.json();
    } catch {
      throw new Error(
        `The server returned an unreadable response with status ${response.status}.`
      );
    }

    if (!response.ok) {
      const detail = typeof payload.detail === "string"
        ? payload.detail
        : "The search could not be completed.";

      throw new Error(detail);
    }

    renderResult(payload);
  } catch (error) {
    console.error(error);

    showError(
      error instanceof Error
        ? error.message
        : "An unexpected search error occurred."
    );
  } finally {
    setLoading(false);
    searchInput.disabled = false;
    searchInput.focus();
  }
}

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch(searchInput.value);
});

exampleButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const query = button.dataset.query ?? "";

    searchInput.value = query;
    runSearch(query);
  });
});
