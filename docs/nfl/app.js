const seasonSelect =
  document.getElementById("season");

const metricSelect =
  document.getElementById("metric");

const conferenceSelect =
  document.getElementById("conference");

const teamSearchSelect =
  document.getElementById("team-search");

const weekLabel =
  document.getElementById("week-label");

const teamCount =
  document.getElementById("team-count");


/*
 * Change this only if your BTB logo lives
 * somewhere else in the docs folder.
 */
const BTB_LOGO_URL =
  "./assets/btb-logo.png";


/*
 * Fixed chart heights are important for Squarespace.
 *
 * Let Plotly respond to changes in WIDTH, but do not
 * allow the chart height to continually recalculate
 * based on the iframe height.
 */
const MAIN_CHART_HEIGHT = 620;
const HISTORY_CHART_HEIGHT = 420;


let currentData = [];
let currentMeta = {};
let historyData = [];


/* ==========================================================
   METRICS
========================================================== */

const METRICS = {

  power: {
    title: "BTB Power Rating",

    x: "off_pts",
    y: "def_pts",

    xLabel: "Offensive Rating",
    yLabel: "Defensive Rating",

    reverseDefense: false,

    format: ".1f"
  },


  wepa: {
    title: "Weighted EPA / Play",

    x: "off_wepa",
    y: "def_wepa",

    xLabel: "Offensive wEPA / Play",
    yLabel: "Defensive wEPA Allowed / Play",

    reverseDefense: true,

    format: ".3f"
  },


  pass_epa: {
    title: "Pass EPA / Play",

    x: "off_pass_epa",
    y: "def_pass_epa",

    xLabel: "Offensive Pass EPA / Play",
    yLabel: "Defensive Pass EPA Allowed / Play",

    reverseDefense: true,

    format: ".3f"
  },


  rush_epa: {
    title: "Rush EPA / Play",

    x: "off_rush_epa",
    y: "def_rush_epa",

    xLabel: "Offensive Rush EPA / Play",
    yLabel: "Defensive Rush EPA Allowed / Play",

    reverseDefense: true,

    format: ".3f"
  },


  eckel: {
    title: "Eckel Rate",

    x: "off_eckel_rate",
    y: "def_eckel_rate",

    xLabel: "Offensive Eckel Rate",
    yLabel: "Defensive Eckel Rate Allowed",

    reverseDefense: true,

    format: ".1%"
  },


  adjusted_pass: {
    title:
      "Opponent-Adjusted Pass EPA / Play",

    x: "adj_off_pass_epa",
    y: "adj_def_pass_epa",

    xLabel:
      "Adjusted Offensive Pass EPA / Play",

    yLabel:
      "Adjusted Defensive Pass EPA / Play",

    reverseDefense: true,

    format: ".3f"
  },


  adjusted_rush: {
    title:
      "Opponent-Adjusted Rush EPA / Play",

    x: "adj_off_rush_epa",
    y: "adj_def_rush_epa",

    xLabel:
      "Adjusted Offensive Rush EPA / Play",

    yLabel:
      "Adjusted Defensive Rush EPA / Play",

    reverseDefense: true,

    format: ".3f"
  }

};


/* ==========================================================
   HELPERS
========================================================== */

function average(values) {

  const valid =
    values.filter(
      value =>
        Number.isFinite(value)
    );

  if (!valid.length) {
    return null;
  }

  return (
    valid.reduce(
      (sum, value) =>
        sum + value,
      0
    ) /
    valid.length
  );
}


function hasValidPair(
  row,
  metric
) {

  const x =
    Number(row[metric.x]);

  const y =
    Number(row[metric.y]);

  return (
    Number.isFinite(x) &&
    Number.isFinite(y)
  );
}


function finiteExtent(values) {

  const valid =
    values.filter(
      Number.isFinite
    );

  if (!valid.length) {
    return null;
  }

  const min =
    Math.min(...valid);

  const max =
    Math.max(...valid);

  const span =
    Math.max(
      max - min,
      1e-9
    );

  return {
    min:
      min - span * 0.06,

    max:
      max + span * 0.06,

    span
  };
}


function signed(
  value,
  digits = 1
) {

  const number =
    Number(value);

  if (!Number.isFinite(number)) {
    return "—";
  }

  return (
    number > 0
      ? "+"
      : ""
  ) + number.toFixed(digits);
}


function movementArrow(value) {

  const n =
    Number(value);

  if (!Number.isFinite(n)) {
    return "";
  }

  if (n > 0) {
    return "▲";
  }

  if (n < 0) {
    return "▼";
  }

  return "—";
}


function escapeHtml(value) {

  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}


/*
 * Force http logo URLs to https.
 *
 * The R pipeline should already do this,
 * but this gives the frontend a second layer
 * of protection.
 */
function safeLogoUrl(value) {

  if (!value) {
    return "";
  }

  return String(value)
    .trim()
    .replace(
      /^http:\/\//i,
      "https://"
    );
}


/* ==========================================================
   LOGOS ON PLOTLY
========================================================== */

function logoImages(
  rows,
  metric,
  highlightedTeam = ""
) {

  const xExtent =
    finiteExtent(
      rows.map(
        row =>
          Number(row[metric.x])
      )
    );

  const yExtent =
    finiteExtent(
      rows.map(
        row =>
          Number(row[metric.y])
      )
    );

  if (!xExtent || !yExtent) {
    return [];
  }

  const sizeX =
    xExtent.span * 0.040;

  const sizeY =
    yExtent.span * 0.065;


  return rows
    .filter(
      row =>
        safeLogoUrl(row.logo)
    )
    .map(
      row => ({

        source:
          safeLogoUrl(row.logo),

        xref:
          "x",

        yref:
          "y",

        x:
          Number(
            row[metric.x]
          ),

        y:
          Number(
            row[metric.y]
          ),

        sizex:
          row.team === highlightedTeam
            ? sizeX * 1.40
            : sizeX,

        sizey:
          row.team === highlightedTeam
            ? sizeY * 1.40
            : sizeY,

        xanchor:
          "center",

        yanchor:
          "middle",

        sizing:
          "contain",

        opacity:
          1,

        layer:
          "above"

      })
    );
}


/* ==========================================================
   LOAD DATA
========================================================== */

async function loadSeason() {

  const season =
    seasonSelect.value;

  const dataPath =
    `./data/${season}/latest.json`;

  const metaPath =
    `./data/${season}/meta.json`;

  const historyPath =
    `./data/${season}/ratings_history.csv`;


  try {

    const response =
      await fetch(
        dataPath,
        {
          cache: "no-store"
        }
      );


    if (!response.ok) {

      throw new Error(
        `Unable to load ${dataPath}: HTTP ${response.status}`
      );

    }


    currentData =
      await response.json();


    if (!Array.isArray(currentData)) {

      throw new Error(
        `${dataPath} did not contain a JSON array`
      );

    }


    /*
     * Meta.
     */
    try {

      const metaResponse =
        await fetch(
          metaPath,
          {
            cache: "no-store"
          }
        );

      currentMeta =
        metaResponse.ok
          ? await metaResponse.json()
          : {};

    } catch {

      currentMeta = {};

    }


    /*
     * Historical ratings.
     */
    try {

      const historyResponse =
        await fetch(
          historyPath,
          {
            cache: "no-store"
          }
        );


      if (historyResponse.ok) {

        const historyText =
          await historyResponse.text();


        const parsed =
          Papa.parse(
            historyText,
            {
              header: true,
              dynamicTyping: true,
              skipEmptyLines: true
            }
          );


        historyData =
          Array.isArray(parsed.data)
            ? parsed.data
            : [];

      } else {

        historyData = [];

      }

    } catch (error) {

      console.warn(
        "Could not load ratings history:",
        error
      );

      historyData = [];

    }


    populateConferences();

    populateTeams();

    updateMetricAvailability();

    renderAll();


  } catch (error) {

    console.error(error);


    currentData = [];
    currentMeta = {};
    historyData = [];


    weekLabel.textContent =
      "Unavailable";

    teamCount.textContent =
      "0";


    document
      .getElementById("chart")
      .innerHTML = `

        <div
          style="
            color:#111;
            padding:40px;
            font-family:Arial;
          "
        >

          Data for ${escapeHtml(season)}
          is not currently available.

          <br><br>

          ${escapeHtml(error.message)}

        </div>

      `;

  }

}


/* ==========================================================
   CONTROLS
========================================================== */

function populateConferences() {

  const existing =
    conferenceSelect.value;


  const conferences = [
    ...new Set(
      currentData
        .map(
          row =>
            row.conference
        )
        .filter(Boolean)
    )
  ].sort();


  conferenceSelect.innerHTML = `
    <option value="ALL">
      All FBS
    </option>
  `;


  conferences.forEach(
    conference => {

      const option =
        document.createElement(
          "option"
        );

      option.value =
        conference;

      option.textContent =
        conference;

      conferenceSelect
        .appendChild(option);

    }
  );


  conferenceSelect.value =
    conferences.includes(existing)
      ? existing
      : "ALL";

}


function populateTeams() {

  const existing =
    teamSearchSelect.value;


  const teams = [
    ...new Set(
      currentData
        .map(
          row =>
            row.team
        )
        .filter(Boolean)
    )
  ].sort();


  teamSearchSelect.innerHTML = `
    <option value="">
      None
    </option>
  `;


  teams.forEach(
    team => {

      const option =
        document.createElement(
          "option"
        );

      option.value =
        team;

      option.textContent =
        team;

      teamSearchSelect
        .appendChild(option);

    }
  );


  if (
    teams.includes(existing)
  ) {

    teamSearchSelect.value =
      existing;

  }

}


function updateMetricAvailability() {

  const availability = {};


  for (
    const [metricKey, metric]
    of Object.entries(METRICS)
  ) {

    availability[metricKey] =
      currentData.some(
        row =>
          hasValidPair(
            row,
            metric
          )
      );

  }


  for (
    const option
    of metricSelect.options
  ) {

    option.disabled =
      availability[
        option.value
      ] !== true;

  }


  const selected =
    metricSelect.options[
      metricSelect.selectedIndex
    ];


  if (
    !selected ||
    selected.disabled
  ) {

    const preferredOrder = [
      "power",
      "wepa",
      "pass_epa",
      "rush_epa",
      "eckel",
      "adjusted_pass",
      "adjusted_rush"
    ];


    const firstAvailable =
      preferredOrder.find(
        key =>
          availability[key]
      );


    if (firstAvailable) {

      metricSelect.value =
        firstAvailable;

    }

  }

}


function updateWeekLabel() {

  const season =
    seasonSelect.value;


  const week =
    currentMeta.thru_week ??
    (
      currentData.length
        ? currentData[0].week
        : null
    );


  if (Number(week) === 0) {

    weekLabel.textContent =
      `${season} Preseason`;

    return;

  }


  if (
    week !== null &&
    week !== undefined
  ) {

    weekLabel.textContent =
      `Week ${week}`;

    return;

  }


  weekLabel.textContent =
    season;

}


/* ==========================================================
   MAIN SCATTERPLOT
========================================================== */

function renderChart() {

  const metricKey =
    metricSelect.value;

  const metric =
    METRICS[metricKey];


  if (!metric) {
    return;
  }


  const conference =
    conferenceSelect.value;


  const highlightedTeam =
    teamSearchSelect.value;


  const benchmark =
    currentData.filter(
      row =>
        hasValidPair(
          row,
          metric
        )
    );


  const displayed =
    benchmark.filter(
      row =>
        conference === "ALL" ||
        row.conference === conference
    );


  if (!benchmark.length) {

    document
      .getElementById("chart")
      .innerHTML = `

        <div
          style="
            color:#111;
            padding:40px;
            font-family:Arial;
          "
        >

          ${escapeHtml(metric.title)}
          data is not yet available
          for this season.

        </div>

      `;


    teamCount.textContent =
      `0 / ${currentData.length} FBS`;


    updateWeekLabel();

    return;

  }


  const benchmarkX =
    average(
      benchmark.map(
        row =>
          Number(
            row[metric.x]
          )
      )
    );


  const benchmarkY =
    average(
      benchmark.map(
        row =>
          Number(
            row[metric.y]
          )
      )
    );


  const normalTeams =
    displayed.filter(
      row =>
        row.team !== highlightedTeam
    );


  const highlighted =
    displayed.filter(
      row =>
        row.team === highlightedTeam
    );


  const traces = [

    {

      type:
        "scatter",

      mode:
        "markers+text",


      x:
        normalTeams.map(
          row =>
            Number(
              row[metric.x]
            )
        ),


      y:
        normalTeams.map(
          row =>
            Number(
              row[metric.y]
            )
        ),


      text:
        normalTeams.map(
          row =>
            safeLogoUrl(row.logo)
              ? ""
              : row.team
        ),


      textposition:
        "top center",


      customdata:
        normalTeams.map(
          row => [

            row.team,
            row.conference,
            row.games,
            row.power_pts,
            row.power_rank,
            row.power_change,
            row.rank_change

          ]
        ),


      /*
       * Transparent marker maintains a large
       * hover target behind each logo.
       */
      marker: {

        size:
          30,

        color:
          normalTeams.map(
            row =>
              safeLogoUrl(row.logo)
                ? "rgba(0,0,0,0.001)"
                : "rgba(31,119,180,0.70)"
          ),

        line: {
          width: 0
        }

      },


      hovertemplate:

        "<b>%{customdata[0]}</b>" +

        "<br>Conference: %{customdata[1]}" +

        "<br>Games: %{customdata[2]}" +

        `<br>${metric.xLabel}: %{x:${metric.format}}` +

        `<br>${metric.yLabel}: %{y:${metric.format}}` +

        (
          metricKey === "power"

          ?

          "<br>BTB Power Rating: %{customdata[3]:.1f}" +

          "<br>Power Rank: #%{customdata[4]}" +

          "<br>Weekly Rating Change: %{customdata[5]:+.1f}" +

          "<br>Rank Movement: %{customdata[6]:+d}"

          :

          ""
        ) +

        "<extra></extra>",


      name:
        "FBS Teams"

    }

  ];


  /*
   * Highlighted-team hover target.
   */
  if (highlighted.length) {

    traces.push({

      type:
        "scatter",

      mode:
        "markers+text",


      x:
        highlighted.map(
          row =>
            Number(
              row[metric.x]
            )
        ),


      y:
        highlighted.map(
          row =>
            Number(
              row[metric.y]
            )
        ),


      text:
        highlighted.map(
          row =>
            safeLogoUrl(row.logo)
              ? ""
              : row.team
        ),


      textposition:
        "top center",


      customdata:
        highlighted.map(
          row => [

            row.team,
            row.conference,
            row.games,
            row.power_pts,
            row.power_rank,
            row.power_change,
            row.rank_change

          ]
        ),


      marker: {

        size:
          46,

        color:
          "rgba(255,255,255,0.01)",

        line: {
          width: 3,
          color: "#111111"
        }

      },


      hovertemplate:

        "<b>%{customdata[0]}</b>" +

        "<br>Conference: %{customdata[1]}" +

        "<br>Games: %{customdata[2]}" +

        `<br>${metric.xLabel}: %{x:${metric.format}}` +

        `<br>${metric.yLabel}: %{y:${metric.format}}` +

        (
          metricKey === "power"

          ?

          "<br>BTB Power Rating: %{customdata[3]:.1f}" +

          "<br>Power Rank: #%{customdata[4]}" +

          "<br>Weekly Rating Change: %{customdata[5]:+.1f}" +

          "<br>Rank Movement: %{customdata[6]:+d}"

          :

          ""
        ) +

        "<extra></extra>",


      name:
        "Highlighted Team"

    });

  }


  const shapes = [];


  const xExtent =
    finiteExtent(
      displayed.map(
        row =>
          Number(
            row[metric.x]
          )
      )
    );


  const yExtent =
    finiteExtent(
      displayed.map(
        row =>
          Number(
            row[metric.y]
          )
      )
    );


  /*
   * Green best quadrant and red worst quadrant.
   *
   * Defensive "allowed" metrics have a reversed
   * y-axis, so the underlying coordinates need
   * to account for that.
   */
  if (
    benchmarkX !== null &&
    benchmarkY !== null &&
    xExtent &&
    yExtent
  ) {

    const bestY0 =
      metric.reverseDefense
        ? yExtent.min
        : benchmarkY;


    const bestY1 =
      metric.reverseDefense
        ? benchmarkY
        : yExtent.max;


    const worstY0 =
      metric.reverseDefense
        ? benchmarkY
        : yExtent.min;


    const worstY1 =
      metric.reverseDefense
        ? yExtent.max
        : benchmarkY;


    shapes.push({

      type:
        "rect",

      xref:
        "x",

      yref:
        "y",

      x0:
        benchmarkX,

      x1:
        xExtent.max,

      y0:
        bestY0,

      y1:
        bestY1,

      fillcolor:
        "rgba(34,197,94,0.10)",

      line: {
        width: 0
      },

      layer:
        "below"

    });


    shapes.push({

      type:
        "rect",

      xref:
        "x",

      yref:
        "y",

      x0:
        xExtent.min,

      x1:
        benchmarkX,

      y0:
        worstY0,

      y1:
        worstY1,

      fillcolor:
        "rgba(239,68,68,0.09)",

      line: {
        width: 0
      },

      layer:
        "below"

    });

  }


  /*
   * All-FBS average lines.
   */
  if (benchmarkX !== null) {

    shapes.push({

      type:
        "line",

      x0:
        benchmarkX,

      x1:
        benchmarkX,

      y0:
        0,

      y1:
        1,

      yref:
        "paper",

      line: {

        dash:
          "dash",

        width:
          1,

        color:
          "#777777"

      }

    });

  }


  if (benchmarkY !== null) {

    shapes.push({

      type:
        "line",

      y0:
        benchmarkY,

      y1:
        benchmarkY,

      x0:
        0,

      x1:
        1,

      xref:
        "paper",

      line: {

        dash:
          "dash",

        width:
          1,

        color:
          "#777777"

      }

    });

  }


  const season =
    seasonSelect.value;


  const layout = {

    title: {

      text:
        `${season} ${metric.title}`,

      x:
        0.5,

      xanchor:
        "center"

    },


    paper_bgcolor:
      "#ffffff",


    plot_bgcolor:
      "#ffffff",


    font: {

      family:
        "Arial, sans-serif",

      color:
        "#111111"

    },


    /*
     * Critical Squarespace fix:
     * fixed chart height prevents iframe
     * auto-height from creating a resize loop.
     */
    autosize:
      true,

    height:
      MAIN_CHART_HEIGHT,


    xaxis: {

      title: {
        text:
          metric.xLabel
      },

      zeroline:
        false,

      showgrid:
        true,

      gridcolor:
        "rgba(0,0,0,0.10)"

    },


    yaxis: {

      title: {
        text:
          metric.yLabel
      },

      autorange:
        metric.reverseDefense
          ? "reversed"
          : true,

      zeroline:
        false,

      showgrid:
        true,

      gridcolor:
        "rgba(0,0,0,0.10)"

    },


    shapes:
      shapes,


    images:
      logoImages(
        displayed,
        metric,
        highlightedTeam
      ),


    hovermode:
      "closest",


    showlegend:
      Boolean(
        highlightedTeam
      ),


    margin: {
      l: 90,
      r: 40,
      t: 80,
      b: 90
    }

  };


  const config = {

    responsive:
      true,


    displaylogo:
      false,


    modeBarButtonsToRemove: [
      "lasso2d",
      "select2d"
    ],


    toImageButtonOptions: {

      format:
        "png",

      filename:
        `btb-${season}-${metricKey}`,

      scale:
        2

    }

  };


  Plotly.react(
    "chart",
    traces,
    layout,
    config
  );


  updateWeekLabel();


  teamCount.textContent =
    `${displayed.length} / ${benchmark.length} FBS`;

}


/* ==========================================================
   WEEKLY MOVERS
========================================================== */

function getMovers() {

  const valid =
    currentData.filter(
      row =>
        Number.isFinite(
          Number(
            row.rank_change
          )
        ) &&
        Number(row.week) > 0
    );


  const risers =
    [...valid]
      .filter(
        row =>
          Number(
            row.rank_change
          ) > 0
      )
      .sort(
        (a, b) => {

          const rankDiff =
            Number(b.rank_change) -
            Number(a.rank_change);

          if (rankDiff !== 0) {
            return rankDiff;
          }

          return (
            Number(b.power_change) -
            Number(a.power_change)
          );

        }
      );


  const fallers =
    [...valid]
      .filter(
        row =>
          Number(
            row.rank_change
          ) < 0
      )
      .sort(
        (a, b) => {

          const rankDiff =
            Number(a.rank_change) -
            Number(b.rank_change);

          if (rankDiff !== 0) {
            return rankDiff;
          }

          return (
            Number(a.power_change) -
            Number(b.power_change)
          );

        }
      );


  return {
    risers,
    fallers
  };

}


function moverRowHtml(
  row,
  type
) {

  const movement =
    Number(
      row.rank_change
    );


  const logo =
    safeLogoUrl(
      row.logo
    );


  return `

    <div class="mover-row">

      <div class="mover-rank">
        #${escapeHtml(row.power_rank)}
      </div>


      ${
        logo

        ?

        `
        <img
          class="mover-logo"
          src="${escapeHtml(logo)}"
          alt=""
          crossorigin="anonymous"
        >
        `

        :

        `<div></div>`
      }


      <div>

        <div class="mover-team">
          ${escapeHtml(row.team)}
        </div>

        <div class="mover-rating">

          BTB Rating:
          ${signed(row.power_pts)}

          &nbsp;|&nbsp;

          Weekly:
          ${signed(row.power_change)}

        </div>

      </div>


      <div
        class="mover-change ${type}"
      >

        ${movementArrow(movement)}
        ${Math.abs(movement)}

      </div>

    </div>

  `;

}


function renderMovers() {

  const {
    risers,
    fallers
  } = getMovers();


  const riserContainer =
    document.getElementById(
      "risers-list"
    );


  const fallerContainer =
    document.getElementById(
      "fallers-list"
    );


  if (
    !riserContainer ||
    !fallerContainer
  ) {
    return;
  }


  if (
    !risers.length &&
    !fallers.length
  ) {

    riserContainer.innerHTML =
      "No weekly movement yet.";

    fallerContainer.innerHTML =
      "No weekly movement yet.";

    return;

  }


  riserContainer.innerHTML =
    risers
      .slice(0, 5)
      .map(
        row =>
          moverRowHtml(
            row,
            "riser"
          )
      )
      .join("");


  fallerContainer.innerHTML =
    fallers
      .slice(0, 5)
      .map(
        row =>
          moverRowHtml(
            row,
            "faller"
          )
      )
      .join("");

}


/* ==========================================================
   POWER RATING HISTORY
========================================================== */

function renderHistoryChart() {

  const containerId =
    "history-chart";


  const container =
    document.getElementById(
      containerId
    );


  if (!container) {
    return;
  }


  const selectedTeam =
    teamSearchSelect.value;


  if (!historyData.length) {

    container.innerHTML =
      "<p>Rating history is not currently available.</p>";

    return;

  }


  let team =
    selectedTeam;


  /*
   * If a team has not been selected,
   * show the current #1 ranked team.
   */
  if (!team) {

    const topTeam =
      [...currentData]
        .filter(
          row =>
            Number.isFinite(
              Number(
                row.power_rank
              )
            )
        )
        .sort(
          (a, b) =>
            Number(a.power_rank) -
            Number(b.power_rank)
        )[0];


    team =
      topTeam?.team || "";

  }


  const rows =
    historyData
      .filter(
        row =>
          row.team === team &&
          Number.isFinite(
            Number(
              row.power_pts
            )
          )
      )
      .sort(
        (a, b) =>
          Number(a.week) -
          Number(b.week)
      );


  if (!rows.length) {

    container.innerHTML =
      "<p>No history available for this team.</p>";

    return;

  }


  const x =
    rows.map(
      row =>
        Number(row.week) === 0
          ? "Preseason"
          : `Week ${row.week}`
    );


  const y =
    rows.map(
      row =>
        Number(
          row.power_pts
        )
    );


  const custom =
    rows.map(
      row => [

        row.power_rank,
        row.power_change,
        row.rank_change

      ]
    );


  const trace = {

    type:
      "scatter",

    mode:
      "lines+markers",

    x,
    y,

    customdata:
      custom,


    hovertemplate:

      "<b>%{x}</b>" +

      "<br>BTB Power Rating: %{y:.1f}" +

      "<br>National Rank: #%{customdata[0]}" +

      "<br>Rating Change: %{customdata[1]:+.1f}" +

      "<br>Rank Movement: %{customdata[2]:+d}" +

      "<extra></extra>"

  };


  const layout = {

    title: {

      text:
        `${team} BTB Power Rating History`,

      x:
        0.5,

      xanchor:
        "center"

    },


    paper_bgcolor:
      "#ffffff",


    plot_bgcolor:
      "#ffffff",


    /*
     * Critical Squarespace fix.
     */
    autosize:
      true,

    height:
      HISTORY_CHART_HEIGHT,


    yaxis: {

      title: {
        text:
          "BTB Power Rating"
      },

      gridcolor:
        "rgba(0,0,0,0.10)"

    },


    xaxis: {

      title: {
        text:
          "Week"
      },

      gridcolor:
        "rgba(0,0,0,0.06)"

    },


    margin: {
      l: 75,
      r: 30,
      t: 70,
      b: 70
    }

  };


  const config = {

    responsive:
      true,

    displaylogo:
      false

  };


  Plotly.react(
    containerId,
    [trace],
    layout,
    config
  );

}


/* ==========================================================
   EXPORT CARD HELPERS
========================================================== */

function getSortedPowerTeams() {

  return [...currentData]

    .filter(
      row =>
        Number.isFinite(
          Number(
            row.power_rank
          )
        ) &&
        Number.isFinite(
          Number(
            row.power_pts
          )
        )
    )

    .sort(
      (a, b) =>
        Number(a.power_rank) -
        Number(b.power_rank)
    );

}


function exportHeaderHtml(
  subtitle
) {

  const season =
    seasonSelect.value;


  return `

    <div class="export-header">

      <div class="export-brand-row">

        <img
          class="export-brand-logo"
          src="${escapeHtml(BTB_LOGO_URL)}"
          alt="BTB Analytics"
          crossorigin="anonymous"
        >

        <div class="export-brand-name">
          BTB Analytics
        </div>

      </div>


      <div class="export-title">
        BTB's ${escapeHtml(season)} Power Ratings
      </div>


      <div class="export-subtitle">
        ${escapeHtml(subtitle)}
      </div>

    </div>

  `;

}


function rankingRowHtml(row) {

  const logo =
    safeLogoUrl(
      row.logo
    );


  return `

    <div class="export-rank-row">

      <div class="export-rank-number">
        ${escapeHtml(row.power_rank)}
      </div>


      <div>

        ${
          logo

          ?

          `
          <img
            class="export-team-logo"
            src="${escapeHtml(logo)}"
            alt=""
            crossorigin="anonymous"
          >
          `

          :

          ""
        }

      </div>


      <div>

        <div class="export-team-name">
          ${escapeHtml(row.team)}
        </div>

        <div class="export-team-meta">
          ${escapeHtml(row.conference || "")}
        </div>

      </div>


      <div class="export-power">

        ${signed(row.power_pts)}

        <span class="export-power-label">
          BTB Rating
        </span>

      </div>

    </div>

  `;

}


function exportMoverBox(
  label,
  row
) {

  if (!row) {

    return `

      <div class="export-mover-box">

        <div class="export-mover-label">
          ${escapeHtml(label)}
        </div>

        <div class="export-mover-team">
          No movement yet
        </div>

      </div>

    `;

  }


  const movement =
    Number(
      row.rank_change
    );


  const logo =
    safeLogoUrl(
      row.logo
    );


  return `

    <div class="export-mover-box">

      <div class="export-mover-label">
        ${escapeHtml(label)}
      </div>


      <div class="export-mover-main">

        ${
          logo

          ?

          `
          <img
            class="export-mover-logo"
            src="${escapeHtml(logo)}"
            alt=""
            crossorigin="anonymous"
          >
          `

          :

          ""
        }


        <div>

          <div class="export-mover-team">
            ${escapeHtml(row.team)}
          </div>


          <div class="export-mover-change">

            ${movementArrow(movement)}
            ${Math.abs(movement)}
            spots

            &nbsp;·&nbsp;

            ${signed(row.power_change)}
            rating

          </div>

        </div>

      </div>

    </div>

  `;

}


/* ==========================================================
   TOP 10 + MOVERS CARD
========================================================== */

function buildTop10Card() {

  const teams =
    getSortedPowerTeams()
      .slice(0, 10);


  const {
    risers,
    fallers
  } = getMovers();


  const card =
    document.getElementById(
      "top10-export-card"
    );


  if (!card) {
    return;
  }


  card.innerHTML = `

    ${exportHeaderHtml(
      "Top 10 Teams"
    )}


    <div class="export-ranking-list">

      ${
        teams
          .map(rankingRowHtml)
          .join("")
      }

    </div>


    <div class="export-movers">

      ${
        exportMoverBox(
          "Biggest Riser",
          risers[0]
        )
      }


      ${
        exportMoverBox(
          "Biggest Faller",
          fallers[0]
        )
      }

    </div>


    <div class="export-footer">

      BTB Analytics · Data through
      ${escapeHtml(
        weekLabel.textContent
      )}

    </div>

  `;

}


/* ==========================================================
   TOP 25 CARD
========================================================== */

function buildTop25Card() {

  const teams =
    getSortedPowerTeams()
      .slice(0, 25);


  const left =
    teams.slice(0, 13);


  const right =
    teams.slice(13, 25);


  const card =
    document.getElementById(
      "top25-export-card"
    );


  if (!card) {
    return;
  }


  card.innerHTML = `

    ${exportHeaderHtml(
      "Top 25 Overall"
    )}


    <div class="export-top25-grid">

      <div class="export-ranking-list">

        ${
          left
            .map(rankingRowHtml)
            .join("")
        }

      </div>


      <div class="export-ranking-list">

        ${
          right
            .map(rankingRowHtml)
            .join("")
        }

      </div>

    </div>


    <div class="export-footer">

      BTB Analytics · Data through
      ${escapeHtml(
        weekLabel.textContent
      )}

    </div>

  `;

}


/* ==========================================================
   MOVERS EXPORT CARD
========================================================== */

function buildMoversCard() {

  const {
    risers,
    fallers
  } = getMovers();


  const card =
    document.getElementById(
      "movers-export-card"
    );


  if (!card) {
    return;
  }


  const riserRows =
    risers
      .slice(0, 5)
      .map(rankingRowHtml)
      .join("");


  const fallerRows =
    fallers
      .slice(0, 5)
      .map(rankingRowHtml)
      .join("");


  card.innerHTML = `

    ${exportHeaderHtml(
      "Weekly Movers"
    )}


    <div class="export-top25-grid">


      <div>

        <div class="export-subtitle">
          Biggest Risers
        </div>

        <div
          class="export-ranking-list"
          style="margin-top:16px;"
        >

          ${
            riserRows ||
            "<div>No movement yet.</div>"
          }

        </div>

      </div>


      <div>

        <div class="export-subtitle">
          Biggest Fallers
        </div>

        <div
          class="export-ranking-list"
          style="margin-top:16px;"
        >

          ${
            fallerRows ||
            "<div>No movement yet.</div>"
          }

        </div>

      </div>


    </div>


    <div class="export-footer">

      BTB Analytics · Data through
      ${escapeHtml(
        weekLabel.textContent
      )}

    </div>

  `;

}


/* ==========================================================
   WAIT FOR EXPORT IMAGES
========================================================== */

async function waitForImages(
  element
) {

  const images =
    [
      ...element.querySelectorAll(
        "img"
      )
    ];


  await Promise.all(
    images.map(
      img => {

        if (
          img.complete &&
          img.naturalWidth > 0
        ) {

          return Promise.resolve();

        }


        return new Promise(
          resolve => {

            const done =
              () =>
                resolve();


            img.addEventListener(
              "load",
              done,
              {
                once: true
              }
            );


            img.addEventListener(
              "error",
              done,
              {
                once: true
              }
            );


            /*
             * Never allow a failed remote image
             * to hang the export forever.
             */
            setTimeout(
              done,
              5000
            );

          }
        );

      }
    )
  );

}


/* ==========================================================
   DOWNLOAD PNG
========================================================== */

async function downloadCard(
  elementId,
  filename
) {

  const element =
    document.getElementById(
      elementId
    );


  const stage =
    document.getElementById(
      "export-stage"
    );


  if (
    !element ||
    !stage
  ) {
    return;
  }


  /*
   * The export stage is normally collapsed to 1px so
   * Squarespace does not include the large 1080/1400px
   * cards in its iframe height calculation.
   *
   * Only while generating the PNG do we make the
   * export area renderable.
   */
  stage.style.visibility =
    "visible";

  stage.style.position =
    "fixed";

  stage.style.left =
    "-5000px";

  stage.style.top =
    "0";

  stage.style.width =
    "auto";

  stage.style.height =
    "auto";

  stage.style.maxHeight =
    "none";

  stage.style.overflow =
    "visible";

  stage.style.pointerEvents =
    "none";


  try {

    await waitForImages(
      element
    );


    /*
     * Give the browser one paint cycle after
     * all of the images have finished.
     */
    await new Promise(
      resolve =>
        requestAnimationFrame(
          () =>
            requestAnimationFrame(
              resolve
            )
        )
    );


    const canvas =
      await html2canvas(
        element,
        {

          scale:
            2,

          backgroundColor:
            null,

          useCORS:
            true,

          allowTaint:
            false,

          logging:
            false

        }
      );


    const link =
      document.createElement(
        "a"
      );


    link.download =
      filename;


    link.href =
      canvas.toDataURL(
        "image/png"
      );


    document.body
      .appendChild(link);


    link.click();


    link.remove();


  } catch (error) {

    console.error(
      "PNG export failed:",
      error
    );


    alert(
      "The PNG could not be generated. Check the browser console for details."
    );


  } finally {

    /*
     * Critical Squarespace fix:
     * collapse the large export cards immediately
     * after the screenshot is finished.
     */
    stage.style.visibility =
      "hidden";

    stage.style.position =
      "absolute";

    stage.style.left =
      "0";

    stage.style.top =
      "0";

    stage.style.width =
      "1px";

    stage.style.height =
      "1px";

    stage.style.maxHeight =
      "1px";

    stage.style.overflow =
      "hidden";

    stage.style.pointerEvents =
      "none";

  }

}


/* ==========================================================
   EXPORT BUTTONS
========================================================== */

const top10Button =
  document.getElementById(
    "download-top10"
  );


if (top10Button) {

  top10Button.addEventListener(
    "click",
    async () => {

      buildTop10Card();

      await downloadCard(
        "top10-export-card",
        `btb-${seasonSelect.value}-power-ratings-top10.png`
      );

    }
  );

}


const top25Button =
  document.getElementById(
    "download-top25"
  );


if (top25Button) {

  top25Button.addEventListener(
    "click",
    async () => {

      buildTop25Card();

      await downloadCard(
        "top25-export-card",
        `btb-${seasonSelect.value}-power-ratings-top25.png`
      );

    }
  );

}


const moversButton =
  document.getElementById(
    "download-movers"
  );


if (moversButton) {

  moversButton.addEventListener(
    "click",
    async () => {

      buildMoversCard();

      await downloadCard(
        "movers-export-card",
        `btb-${seasonSelect.value}-weekly-movers.png`
      );

    }
  );

}


/* ==========================================================
   RENDER EVERYTHING
========================================================== */

function renderAll() {

  renderChart();

  renderMovers();

  renderHistoryChart();

}


/* ==========================================================
   EVENTS
========================================================== */

seasonSelect
  .addEventListener(
    "change",
    loadSeason
  );


metricSelect
  .addEventListener(
    "change",
    renderChart
  );


conferenceSelect
  .addEventListener(
    "change",
    renderChart
  );


teamSearchSelect
  .addEventListener(
    "change",
    () => {

      renderChart();

      renderHistoryChart();

    }
  );


/* ==========================================================
   INITIAL LOAD
========================================================== */

loadSeason();
