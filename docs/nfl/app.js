const IS_SQUARESPACE_EMBED =
  new URLSearchParams(window.location.search)
    .get("embed") === "1";

if (IS_SQUARESPACE_EMBED) {
  document.documentElement.classList.add(
    "squarespace-embed"
  );
}

const seasonSelect =
  document.getElementById("season");

const metricSelect =
  document.getElementById("metric");

const conferenceSelect =
  document.getElementById("conference");

const divisionSelect =
  document.getElementById("division");

const teamSearchSelect =
  document.getElementById("team-search");

const weekLabel =
  document.getElementById("week-label");

const teamCount =
  document.getElementById("team-count");


const BTB_LOGO_URL =
  "https://raw.githubusercontent.com/trashduty/football-testgrounds/main/btb-logo.png";


let currentData = [];
let currentMeta = {};
let historyData = [];


const METRICS = {

  power: {
    title: "BTB Power Rating",

    x: "off_rt",
    y: "def_good",

    xLabel:
      "Offensive Rating",

    yLabel:
      "Defensive Rating",

    reverseDefense:
      false,

    format:
      ".1f",

    quadrants:
      true
  },


  wepa: {
    title:
      "Weighted EPA",

    x:
      "off_wepa",

    y:
      "def_wepa",

    xLabel:
      "Offensive Weighted EPA",

    yLabel:
      "Defensive Weighted EPA Allowed",

    reverseDefense:
      true,

    format:
      ".2f",

    quadrants:
      true
  },


  epa: {
    title:
      "EPA / Play",

    x:
      "off_epa",

    y:
      "def_epa",

    xLabel:
      "Offensive EPA / Play",

    yLabel:
      "Defensive EPA / Play Allowed",

    reverseDefense:
      true,

    format:
      ".3f",

    quadrants:
      true
  },


  pass_epa: {
    title:
      "Pass EPA / Play",

    x:
      "off_pass_epa",

    y:
      "def_pass_epa",

    xLabel:
      "Offensive Pass EPA / Play",

    yLabel:
      "Defensive Pass EPA / Play Allowed",

    reverseDefense:
      true,

    format:
      ".3f",

    quadrants:
      true
  },


  rush_epa: {
    title:
      "Rush EPA / Play",

    x:
      "off_rush_epa",

    y:
      "def_rush_epa",

    xLabel:
      "Offensive Rush EPA / Play",

    yLabel:
      "Defensive Rush EPA / Play Allowed",

    reverseDefense:
      true,

    format:
      ".3f",

    quadrants:
      true
  },


  eckel: {
    title:
      "Eckel Rate",

    x:
      "off_eckel_rate",

    y:
      "def_eckel_rate",

    xLabel:
      "Offensive Eckel Rate",

    yLabel:
      "Defensive Eckel Rate Allowed",

    reverseDefense:
      true,

    format:
      ".1%",

    quadrants:
      true
  },


  success: {
    title:
      "Success Rate",

    x:
      "off_success",

    y:
      "def_success",

    xLabel:
      "Offensive Success Rate",

    yLabel:
      "Defensive Success Rate Allowed",

    reverseDefense:
      true,

    format:
      ".1%",

    quadrants:
      true
  },


  proe: {
    title:
      "Pass Rate Over Expected",

    x:
      "proe",

    y:
      "def_proe",

    xLabel:
      "Offensive PROE",

    yLabel:
      "Opponent PROE vs Defense",

    reverseDefense:
      false,

    format:
      ".1%",

    quadrants:
      false
  }

};


function average(values) {

  const valid =
    values.filter(
      Number.isFinite
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


function finiteExtent(values) {

  const valid =
    values.filter(
      Number.isFinite
    );


  if (!valid.length) {
    return null;
  }


  const min =
    Math.min(
      ...valid
    );


  const max =
    Math.max(
      ...valid
    );


  const span =
    Math.max(
      max - min,
      1e-9
    );


  return {
    min:
      min -
      span * 0.06,

    max:
      max +
      span * 0.06,

    span
  };

}


function hasValidPair(
  row,
  metric
) {

  return (
    Number.isFinite(
      Number(
        row[
          metric.x
        ]
      )
    ) &&
    Number.isFinite(
      Number(
        row[
          metric.y
        ]
      )
    )
  );

}


function signed(
  value,
  digits = 1
) {

  const n =
    Number(value);


  if (!Number.isFinite(n)) {
    return "—";
  }


  return (
    `${n > 0 ? "+" : ""}${n.toFixed(digits)}`
  );

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

  return String(
    value ?? ""
  )
    .replaceAll(
      "&",
      "&amp;"
    )
    .replaceAll(
      "<",
      "&lt;"
    )
    .replaceAll(
      ">",
      "&gt;"
    )
    .replaceAll(
      '"',
      "&quot;"
    )
    .replaceAll(
      "'",
      "&#039;"
    );

}


function displayName(row) {

  return (
    row.team_name ||
    row.team ||
    "Unknown Team"
  );

}


function logoImages(
  rows,
  metric,
  highlightedTeam = ""
) {

  const xExtent =
    finiteExtent(
      rows.map(
        row =>
          Number(
            row[
              metric.x
            ]
          )
      )
    );


  const yExtent =
    finiteExtent(
      rows.map(
        row =>
          Number(
            row[
              metric.y
            ]
          )
      )
    );


  if (
    !xExtent ||
    !yExtent
  ) {

    return [];

  }


  const sizeX =
    xExtent.span *
    0.06;


  const sizeY =
    yExtent.span *
    0.08;


  return rows
    .filter(
      row =>
        typeof row.logo ===
          "string" &&
        row.logo.trim() !==
          ""
    )
    .map(
      row => ({

        source:
          row.logo,

        xref:
          "x",

        yref:
          "y",

        x:
          Number(
            row[
              metric.x
            ]
          ),

        y:
          Number(
            row[
              metric.y
            ]
          ),

        sizex:
          row.team ===
            highlightedTeam
            ? sizeX * 1.45
            : sizeX,

        sizey:
          row.team ===
            highlightedTeam
            ? sizeY * 1.45
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
          cache:
            "no-store"
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


    try {

      const metaResponse =
        await fetch(
          metaPath,
          {
            cache:
              "no-store"
          }
        );


      currentMeta =
        metaResponse.ok
          ? await metaResponse.json()
          : {};

    } catch {

      currentMeta = {};

    }


    try {

      const historyResponse =
        await fetch(
          historyPath,
          {
            cache:
              "no-store"
          }
        );


      if (historyResponse.ok) {

        const historyText =
          await historyResponse.text();


        const parsed =
          Papa.parse(
            historyText,
            {
              header:
                true,

              dynamicTyping:
                true,

              skipEmptyLines:
                true
            }
          );


        historyData =
          Array.isArray(
            parsed.data
          )
            ? parsed.data
            : [];

      } else {

        historyData = [];

      }

    } catch (error) {

      console.warn(
        "Could not load NFL ratings history:",
        error
      );


      historyData = [];

    }


    populateConferences();

    populateDivisions();

    populateTeams();

    updateMetricAvailability();

    renderAll();


  } catch (error) {

    console.error(
      error
    );


    currentData = [];

    currentMeta = {};

    historyData = [];


    weekLabel.textContent =
      "Unavailable";


    teamCount.textContent =
      "0";


    document
      .getElementById(
        "chart"
      )
      .innerHTML = `

        <div class="chart-error">

          NFL data for
          ${escapeHtml(season)}
          is not currently available.

          <br><br>

          ${escapeHtml(error.message)}

        </div>

      `;


    document
      .getElementById(
        "risers-list"
      )
      .innerHTML =
        `
        <div class="mover-empty">
          Data unavailable.
        </div>
        `;


    document
      .getElementById(
        "fallers-list"
      )
      .innerHTML =
        `
        <div class="mover-empty">
          Data unavailable.
        </div>
        `;


    document
      .getElementById(
        "history-chart"
      )
      .innerHTML =
        `
        <p>
          Rating history is not currently available.
        </p>
        `;

  }

}


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
        .filter(
          Boolean
        )
    )
  ].sort();


  conferenceSelect.innerHTML =
    `
    <option value="ALL">
      All NFL
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
        .appendChild(
          option
        );

    }
  );


  conferenceSelect.value =
    conferences.includes(
      existing
    )
      ? existing
      : "ALL";

}


function populateDivisions() {

  const existing =
    divisionSelect.value;


  const conference =
    conferenceSelect.value;


  const divisions = [
    ...new Set(
      currentData
        .filter(
          row =>
            conference === "ALL" ||
            row.conference ===
              conference
        )
        .map(
          row =>
            row.division
        )
        .filter(
          Boolean
        )
    )
  ].sort();


  divisionSelect.innerHTML =
    `
    <option value="ALL">
      All Divisions
    </option>
    `;


  divisions.forEach(
    division => {

      const option =
        document.createElement(
          "option"
        );


      option.value =
        division;


      option.textContent =
        division;


      divisionSelect
        .appendChild(
          option
        );

    }
  );


  divisionSelect.value =
    divisions.includes(
      existing
    )
      ? existing
      : "ALL";

}


function populateTeams() {

  const existing =
    teamSearchSelect.value;


  const teams =
    [...currentData]
      .filter(
        row =>
          row.team
      )
      .sort(
        (a, b) =>
          displayName(a)
            .localeCompare(
              displayName(b)
            )
      );


  teamSearchSelect.innerHTML =
    `
    <option value="">
      None
    </option>
    `;


  teams.forEach(
    row => {

      const option =
        document.createElement(
          "option"
        );


      option.value =
        row.team;


      option.textContent =
        displayName(
          row
        );


      teamSearchSelect
        .appendChild(
          option
        );

    }
  );


  if (
    teams.some(
      row =>
        row.team ===
          existing
    )
  ) {

    teamSearchSelect.value =
      existing;

  }

}


function updateMetricAvailability() {

  const availability = {};


  for (
    const [
      key,
      metric
    ]
    of Object.entries(
      METRICS
    )
  ) {

    availability[key] =
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
      ] !==
      true;

  }


  const selected =
    metricSelect.options[
      metricSelect.selectedIndex
    ];


  if (
    !selected ||
    selected.disabled
  ) {

    const firstAvailable =
      Object.keys(
        METRICS
      )
      .find(
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

  const enteringWeek =
    Number(
      currentMeta.rating_entering_week
    );


  if (
    Number.isFinite(
      enteringWeek
    )
  ) {

    weekLabel.textContent =
      `Entering Week ${enteringWeek}`;

    return;

  }


  const rowWeek =
    currentData.length
      ? Number(
          currentData[0].week
        )
      : NaN;


  weekLabel.textContent =
    Number.isFinite(
      rowWeek
    )
      ? `Entering Week ${rowWeek}`
      : seasonSelect.value;

}


function filteredRows(metric) {

  const conference =
    conferenceSelect.value;


  const division =
    divisionSelect.value;


  return currentData.filter(
    row =>
      hasValidPair(
        row,
        metric
      ) &&
      (
        conference === "ALL" ||
        row.conference ===
          conference
      ) &&
      (
        division === "ALL" ||
        row.division ===
          division
      )
  );

}


function renderChart() {

  const metricKey =
    metricSelect.value;


  const metric =
    METRICS[
      metricKey
    ];


  if (!metric) {
    return;
  }


  const benchmark =
    currentData.filter(
      row =>
        hasValidPair(
          row,
          metric
        )
    );


  const displayed =
    filteredRows(
      metric
    );


  const highlightedTeam =
    teamSearchSelect.value;


  if (!benchmark.length) {

    document
      .getElementById(
        "chart"
      )
      .innerHTML =
        `
        <div class="chart-error">
          ${escapeHtml(metric.title)}
          data is not available.
        </div>
        `;


    teamCount.textContent =
      "0";


    updateWeekLabel();

    return;

  }


  const benchmarkX =
    average(
      benchmark.map(
        row =>
          Number(
            row[
              metric.x
            ]
          )
      )
    );


  const benchmarkY =
    average(
      benchmark.map(
        row =>
          Number(
            row[
              metric.y
            ]
          )
      )
    );


  const normalTeams =
    displayed.filter(
      row =>
        row.team !==
          highlightedTeam
    );


  const highlighted =
    displayed.filter(
      row =>
        row.team ===
          highlightedTeam
    );


  function makeTrace(
    rows,
    highlight = false
  ) {

    return {

      type:
        "scatter",

      mode:
        "markers+text",


      x:
        rows.map(
          row =>
            Number(
              row[
                metric.x
              ]
            )
        ),


      y:
        rows.map(
          row =>
            Number(
              row[
                metric.y
              ]
            )
        ),


      text:
        rows.map(
          row =>
            row.logo
              ? ""
              : row.team
        ),


      textposition:
        "top center",


      customdata:
        rows.map(
          row => [

            displayName(row),

            row.team,

            row.conference,

            row.division,

            row.games,

            row.power_final,

            row.power_rank,

            row.off_rt,

            row.def_good,

            row.qb_value,

            row.QBname,

            row.power_change,

            row.rank_change

          ]
        ),


      marker: {

        size:
          highlight
            ? 42
            : 28,


        color:
          highlight
            ? "rgba(255,255,255,0.01)"
            : rows.map(
                row =>
                  row.logo
                    ? "rgba(0,0,0,0.01)"
                    : "rgba(31,119,180,0.70)"
              ),


        line:
          highlight
            ? {
                width:
                  3,

                color:
                  "#111111"
              }
            : {
                width:
                  0
              }

      },


      hovertemplate:

        "<b>%{customdata[0]}</b> (%{customdata[1]})" +

        "<br>%{customdata[2]} · %{customdata[3]}" +

        "<br>Games entering week: %{customdata[4]}" +

        `<br>${metric.xLabel}: %{x:${metric.format}}` +

        `<br>${metric.yLabel}: %{y:${metric.format}}` +

        "<br>BTB Power Rating: %{customdata[5]:+.1f}" +

        "<br>Power Rank: #%{customdata[6]}" +

        "<br>Offense: %{customdata[7]:+.1f}" +

        "<br>Defense: %{customdata[8]:+.1f}" +

        "<br>QB Value: %{customdata[9]:+.1f}" +

        "<br>Starter: %{customdata[10]}" +

        "<br>Weekly Rating Change: %{customdata[11]:+.1f}" +

        "<br>Rank Movement: %{customdata[12]:+d}" +

        "<extra></extra>",


      name:
        highlight
          ? "Highlighted Team"
          : "NFL Teams"

    };

  }


  const traces = [
    makeTrace(
      normalTeams
    )
  ];


  if (highlighted.length) {

    traces.push(
      makeTrace(
        highlighted,
        true
      )
    );

  }


  const shapes = [];


  const xExtent =
    finiteExtent(
      displayed.map(
        row =>
          Number(
            row[
              metric.x
            ]
          )
      )
    );


  const yExtent =
    finiteExtent(
      displayed.map(
        row =>
          Number(
            row[
              metric.y
            ]
          )
      )
    );


  if (
    metric.quadrants &&
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
        width:
          0
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
        width:
          0
      },

      layer:
        "below"

    });

  }


  if (
    benchmarkX !==
      null
  ) {

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


  if (
    benchmarkY !==
      null
  ) {

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
        `${season} NFL ${metric.title}`,

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

      l:
        95,

      r:
        45,

      t:
        80,

      b:
        90

    },


    autosize:
      true

  };


  Plotly.react(

    "chart",

    traces,

    layout,

    {

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
          `btb-${season}-nfl-${metricKey}`,

        scale:
          2

      }

    }

  );


  updateWeekLabel();


  teamCount.textContent =
    `${displayed.length} / ${benchmark.length} NFL`;

}


function getMovers() {

  const valid =
    currentData.filter(
      row =>
        Number.isFinite(
          Number(
            row.power_change
          )
        ) &&
        Number.isFinite(
          Number(
            row.rank_change
          )
        ) &&
        Number(
          row.week
        ) >
        1
    );


  return {

    risers:
      [...valid]
        .filter(
          row =>
            Number(
              row.power_change
            ) >
            0
        )
        .sort(
          (a, b) =>
            Number(
              b.power_change
            ) -
            Number(
              a.power_change
            )
        ),


    fallers:
      [...valid]
        .filter(
          row =>
            Number(
              row.power_change
            ) <
            0
        )
        .sort(
          (a, b) =>
            Number(
              a.power_change
            ) -
            Number(
              b.power_change
            )
        )

  };

}


function moverRowHtml(
  row,
  listRank
) {

  const ratingChange =
    Number(
      row.power_change
    );


  const rankChange =
    Number(
      row.rank_change
    );


  const ratingClass =
    ratingChange >= 0
      ? "positive"
      : "negative";


  const rankClass =
    rankChange >= 0
      ? "positive"
      : "negative";


  return `

    <div class="mover-row">

      <div class="mover-list-rank">
        ${listRank}
      </div>


      <div class="mover-logo-wrap">

        ${
          row.logo
          ?
          `
          <img
            class="mover-logo"
            src="${escapeHtml(row.logo)}"
            alt="${escapeHtml(displayName(row))} logo"
            crossorigin="anonymous"
          >
          `
          :
          `
          <div class="mover-logo-placeholder">
            ${escapeHtml(row.team || "?")}
          </div>
          `
        }

      </div>


      <div class="mover-team-block">

        <div class="mover-team">
          ${escapeHtml(displayName(row))}
        </div>

        <div class="mover-rating">

          #${escapeHtml(row.power_rank)}
          overall
          ·
          BTB Rating
          ${signed(row.power_final)}

        </div>

      </div>


      <div class="mover-stat-block">

        <div class="mover-primary-change ${ratingClass}">
          ${signed(ratingChange)}
        </div>

        <div class="mover-stat-label">
          BTB pts
        </div>

      </div>


      <div class="mover-stat-block mover-rank-move">

        <div class="mover-secondary-change ${rankClass}">

          ${movementArrow(rankChange)}
          ${Math.abs(rankChange)}

        </div>

        <div class="mover-stat-label">
          rank
        </div>

      </div>

    </div>

  `;

}


function renderMovers() {

  const {
    risers,
    fallers
  } =
    getMovers();


  const emptyHtml =
    `
    <div class="mover-empty">
      No weekly movement yet.
    </div>
    `;


  document
    .getElementById(
      "risers-list"
    )
    .innerHTML =
      risers.length
        ? risers
            .slice(
              0,
              5
            )
            .map(
              (
                row,
                index
              ) =>
                moverRowHtml(
                  row,
                  index + 1
                )
            )
            .join("")
        : emptyHtml;


  document
    .getElementById(
      "fallers-list"
    )
    .innerHTML =
      fallers.length
        ? fallers
            .slice(
              0,
              5
            )
            .map(
              (
                row,
                index
              ) =>
                moverRowHtml(
                  row,
                  index + 1
                )
            )
            .join("")
        : emptyHtml;

}


function renderHistoryChart() {

  const containerId =
    "history-chart";


  if (!historyData.length) {

    document
      .getElementById(
        containerId
      )
      .innerHTML =
        `
        <p>
          Rating history is not currently available.
        </p>
        `;

    return;

  }


  let team =
    teamSearchSelect.value;


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
            Number(
              a.power_rank
            ) -
            Number(
              b.power_rank
            )
        )[0];


    team =
      topTeam?.team ||
      "";

  }


  const rows =
    historyData
      .filter(
        row =>
          row.team ===
            team &&
          Number.isFinite(
            Number(
              row.power_final
            )
          )
      )
      .sort(
        (a, b) =>
          Number(
            a.week
          ) -
          Number(
            b.week
          )
      );


  if (!rows.length) {

    document
      .getElementById(
        containerId
      )
      .innerHTML =
        `
        <p>
          No rating history is available for this team.
        </p>
        `;

    return;

  }


  const teamName =
    rows[
      rows.length -
      1
    ].team_name ||
    team;


  Plotly.react(

    containerId,

    [

      {

        type:
          "scatter",

        mode:
          "lines+markers",


        x:
          rows.map(
            row =>
              `Entering Week ${row.week}`
          ),


        y:
          rows.map(
            row =>
              Number(
                row.power_final
              )
          ),


        customdata:
          rows.map(
            row => [

              row.power_rank,

              row.power_change,

              row.rank_change,

              row.QBname,

              row.qb_value

            ]
          ),


        hovertemplate:

          "<b>%{x}</b>" +

          "<br>BTB Power Rating: %{y:+.1f}" +

          "<br>National Rank: #%{customdata[0]}" +

          "<br>Rating Change: %{customdata[1]:+.1f}" +

          "<br>Rank Movement: %{customdata[2]:+d}" +

          "<br>Starter: %{customdata[3]}" +

          "<br>QB Value: %{customdata[4]:+.1f}" +

          "<extra></extra>"

      }

    ],

    {

      title: {

        text:
          `${teamName} BTB Power Rating History`,

        x:
          0.5

      },


      paper_bgcolor:
        "#ffffff",


      plot_bgcolor:
        "#ffffff",


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
            "Rating Snapshot"
        },

        gridcolor:
          "rgba(0,0,0,0.06)"

      },


      margin: {

        l:
          75,

        r:
          30,

        t:
          70,

        b:
          90

      }

    },

    {

      responsive:
        true,

      displaylogo:
        false

    }

  );

}


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
            row.power_final
          )
        )
    )

    .sort(
      (a, b) =>
        Number(
          a.power_rank
        ) -
        Number(
          b.power_rank
        )
    );

}


function exportHeaderHtml(
  subtitle
) {

  return `

    <div class="export-header">

      <div class="export-brand-row">

        <img
          class="export-brand-logo"
          src="${BTB_LOGO_URL}"
          alt="BTB Analytics"
          crossorigin="anonymous"
        >

      </div>


      <div class="export-title">

        BTB's
        ${seasonSelect.value}
        NFL Power Ratings

      </div>


      <div class="export-subtitle">
        ${escapeHtml(subtitle)}
      </div>

    </div>

  `;

}


function rankingRowHtml(row) {

  return `

    <div class="export-rank-row">

      <div class="export-rank-number">
        ${escapeHtml(row.power_rank)}
      </div>


      <div class="export-logo-cell">

        ${
          row.logo
          ?
          `
          <img
            class="export-team-logo"
            src="${escapeHtml(row.logo)}"
            alt="${escapeHtml(displayName(row))} logo"
            crossorigin="anonymous"
          >
          `
          :
          ""
        }

      </div>


      <div>

        <div class="export-team-name">
          ${escapeHtml(displayName(row))}
        </div>

        <div class="export-team-meta">
          ${escapeHtml(row.division || row.conference || "")}
        </div>

      </div>


      <div class="export-power">

        ${signed(row.power_final)}

        <span class="export-power-label">
          BTB Rating
        </span>

      </div>

    </div>

  `;

}


function top10RankingRowHtml(row) {

  const ratingChange =
    Number(
      row.power_change
    );


  const rankChange =
    Number(
      row.rank_change
    );


  const ratingClass =
    ratingChange > 0
      ? "export-change-positive"
      : ratingChange < 0
        ? "export-change-negative"
        : "export-change-neutral";


  const rankClass =
    rankChange > 0
      ? "export-change-positive"
      : rankChange < 0
        ? "export-change-negative"
        : "export-change-neutral";


  return `

    <div class="export-top10-row">

      <div class="export-rank-number">
        ${escapeHtml(row.power_rank)}
      </div>


      <div class="export-top10-logo-cell">

        ${
          row.logo
          ?
          `
          <img
            class="export-team-logo"
            src="${escapeHtml(row.logo)}"
            alt="${escapeHtml(displayName(row))} logo"
            crossorigin="anonymous"
          >
          `
          :
          ""
        }

      </div>


      <div class="export-top10-team">

        <div class="export-team-name">
          ${escapeHtml(displayName(row))}
        </div>

        <div class="export-team-meta">

          ${escapeHtml(row.division || "")}

          ·

          ${escapeHtml(row.QBname || "")}

        </div>

      </div>


      <div class="export-top10-stat">

        <div class="export-top10-stat-value">
          ${signed(row.power_final)}
        </div>

        <span>
          BTB Rating
        </span>

      </div>


      <div class="export-top10-stat">

        <div class="${ratingClass}">
          ${signed(ratingChange)}
        </div>

        <span>
          BTB pts
        </span>

      </div>


      <div class="export-top10-stat">

        <div class="${rankClass}">

          ${movementArrow(rankChange)}
          ${Math.abs(rankChange)}

        </div>

        <span>
          rank
        </span>

      </div>

    </div>

  `;

}


function exportMoverRowHtml(
  row,
  listRank,
  type
) {

  const ratingChange =
    Number(
      row.power_change
    );


  const rankChange =
    Number(
      row.rank_change
    );


  const changeClass =
    type === "riser"
      ? "export-change-positive"
      : "export-change-negative";


  const rankBackgroundClass =
    type === "faller"
      ? "negative"
      : "";


  return `

    <div class="export-mover-row">

      <div class="export-mover-rank ${rankBackgroundClass}">
        ${listRank}
      </div>


      <div class="export-mover-logo-cell">

        ${
          row.logo
          ?
          `
          <img
            class="export-mover-list-logo"
            src="${escapeHtml(row.logo)}"
            alt="${escapeHtml(displayName(row))} logo"
            crossorigin="anonymous"
          >
          `
          :
          `
          <div class="export-mover-logo-fallback">
            ${escapeHtml(row.team || "?")}
          </div>
          `
        }

      </div>


      <div class="export-mover-copy">

        <div class="export-mover-list-team">
          ${escapeHtml(displayName(row))}
        </div>

        <div class="export-mover-list-meta">

          #${escapeHtml(row.power_rank)}
          overall

          ·

          BTB Rating
          ${signed(row.power_final)}

        </div>

      </div>


      <div class="export-mover-stat">

        <div class="${changeClass}">
          ${signed(ratingChange)}
        </div>

        <span>
          BTB pts
        </span>

      </div>


      <div class="export-mover-stat export-mover-rank-stat">

        <div class="${changeClass}">

          ${movementArrow(rankChange)}
          ${Math.abs(rankChange)}

        </div>

        <span>
          rank
        </span>

      </div>

    </div>

  `;

}


function exportMoverEmptyHtml() {

  return `
    <div class="export-mover-empty">
      No weekly movement yet.
    </div>
  `;

}


function exportFooterHtml() {

  return `

    <div class="export-footer">

      BTB Analytics
      ·
      ${escapeHtml(weekLabel.textContent)}

    </div>

  `;

}


function buildTop10Card() {

  const teams =
    getSortedPowerTeams()
      .slice(
        0,
        10
      );


  document
    .getElementById(
      "top10-export-card"
    )
    .innerHTML = `

      ${exportHeaderHtml(
        "Top 10 Teams"
      )}


      <div class="export-ranking-list">

        ${
          teams
            .map(
              top10RankingRowHtml
            )
            .join("")
        }

      </div>


      ${exportFooterHtml()}

    `;

}


function buildTop25Card() {

  const teams =
    getSortedPowerTeams()
      .slice(
        0,
        25
      );


  const left =
    teams.slice(
      0,
      13
    );


  const right =
    teams.slice(
      13,
      25
    );


  document
    .getElementById(
      "top25-export-card"
    )
    .innerHTML = `

      ${exportHeaderHtml(
        "Top 25 Overall"
      )}


      <div class="export-top25-grid">

        <div class="export-ranking-list">

          ${
            left
              .map(
                rankingRowHtml
              )
              .join("")
          }

        </div>


        <div class="export-ranking-list">

          ${
            right
              .map(
                rankingRowHtml
              )
              .join("")
          }

        </div>

      </div>


      ${exportFooterHtml()}

    `;

}


function buildMoversCard() {

  const {
    risers,
    fallers
  } =
    getMovers();


  const riserRows =
    risers
      .slice(
        0,
        5
      )
      .map(
        (
          row,
          index
        ) =>
          exportMoverRowHtml(
            row,
            index + 1,
            "riser"
          )
      )
      .join("");


  const fallerRows =
    fallers
      .slice(
        0,
        5
      )
      .map(
        (
          row,
          index
        ) =>
          exportMoverRowHtml(
            row,
            index + 1,
            "faller"
          )
      )
      .join("");


  document
    .getElementById(
      "movers-export-card"
    )
    .innerHTML = `

      ${exportHeaderHtml(
        "Weekly Movers"
      )}


      <div class="export-mover-columns">

        <div class="export-mover-column">

          <div class="export-mover-column-title">
            Biggest Risers
          </div>

          <div class="export-mover-list">

            ${
              riserRows ||
              exportMoverEmptyHtml()
            }

          </div>

        </div>


        <div class="export-mover-column">

          <div class="export-mover-column-title negative">
            Biggest Fallers
          </div>

          <div class="export-mover-list">

            ${
              fallerRows ||
              exportMoverEmptyHtml()
            }

          </div>

        </div>

      </div>


      ${exportFooterHtml()}

    `;

}


async function waitForExportImages(element) {

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
          img.naturalWidth >
            0
        ) {

          return Promise.resolve();

        }


        return new Promise(
          resolve => {

            img.onload =
              resolve;

            img.onerror =
              resolve;

          }
        );

      }
    )

  );

}


async function downloadCard(
  elementId,
  filename
) {

  const element =
    document.getElementById(
      elementId
    );


  if (!element) {
    return;
  }


  await waitForExportImages(
    element
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


  link.click();

}


document
  .getElementById(
    "download-top10"
  )
  .addEventListener(
    "click",
    async () => {

      buildTop10Card();


      await downloadCard(

        "top10-export-card",

        `btb-${seasonSelect.value}-nfl-power-ratings-top10.png`

      );

    }
  );


document
  .getElementById(
    "download-top25"
  )
  .addEventListener(
    "click",
    async () => {

      buildTop25Card();


      await downloadCard(

        "top25-export-card",

        `btb-${seasonSelect.value}-nfl-power-ratings-top25.png`

      );

    }
  );


document
  .getElementById(
    "download-movers"
  )
  .addEventListener(
    "click",
    async () => {

      buildMoversCard();


      await downloadCard(

        "movers-export-card",

        `btb-${seasonSelect.value}-nfl-weekly-movers.png`

      );

    }
  );


function renderAll() {

  renderChart();

  renderMovers();

  renderHistoryChart();

}


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
    () => {

      populateDivisions();

      renderChart();

    }
  );


divisionSelect
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


loadSeason();
