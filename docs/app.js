const seasonSelect = document.getElementById("season");
const metricSelect = document.getElementById("metric");
const conferenceSelect = document.getElementById("conference");
const teamSearchSelect = document.getElementById("team-search");

const weekLabel = document.getElementById("week-label");
const teamCount = document.getElementById("team-count");

let currentData = [];
let currentMeta = {};


const METRICS = {

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
        title: "Opponent-Adjusted Pass EPA / Play",
        x: "adj_off_pass_epa",
        y: "adj_def_pass_epa",
        xLabel: "Adjusted Offensive Pass EPA / Play",
        yLabel: "Adjusted Defensive Pass EPA / Play",
        reverseDefense: true,
        format: ".3f"
    },

    adjusted_rush: {
        title: "Opponent-Adjusted Rush EPA / Play",
        x: "adj_off_rush_epa",
        y: "adj_def_rush_epa",
        xLabel: "Adjusted Offensive Rush EPA / Play",
        yLabel: "Adjusted Defensive Rush EPA / Play",
        reverseDefense: true,
        format: ".3f"
    },

    power: {
        title: "BTB Power Rating",
        x: "off_pts",
        y: "def_pts",
        xLabel: "Offensive Rating",
        yLabel: "Defensive Rating",
        reverseDefense: false,
        format: ".1f"
    }

};


function average(values) {

    const valid = values.filter(
        value => Number.isFinite(value)
    );

    if (!valid.length) {
        return null;
    }

    return (
        valid.reduce(
            (sum, value) => sum + value,
            0
        )
        / valid.length
    );
}


async function loadSeason() {

    const season = seasonSelect.value;

    const dataPath =
        `./data/${season}/latest.json`;

    const metaPath =
        `./data/${season}/meta.json`;

    try {

        const response =
            await fetch(dataPath, {
                cache: "no-store"
            });

        if (!response.ok) {
            throw new Error(
                `Unable to load ${dataPath}`
            );
        }

        currentData =
            await response.json();


        try {

            const metaResponse =
                await fetch(metaPath, {
                    cache: "no-store"
                });

            currentMeta =
                metaResponse.ok
                    ? await metaResponse.json()
                    : {};

        } catch {

            currentMeta = {};
        }


        populateConferences();
        populateTeams();
        renderChart();

    } catch (error) {

        console.error(error);

        document.getElementById(
            "chart"
        ).innerHTML =
            `<div style="
                color:#111;
                padding:40px;
                font-family:Arial;
            ">
                Data for ${season} is not currently available.
            </div>`;
    }
}


function populateConferences() {

    const existing =
        conferenceSelect.value;

    const conferences = [
        ...new Set(
            currentData
                .map(row => row.conference)
                .filter(Boolean)
        )
    ].sort();


    conferenceSelect.innerHTML =
        `<option value="ALL">
            All FBS
        </option>`;


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

            conferenceSelect.appendChild(
                option
            );
        }
    );


    if (
        conferences.includes(existing)
    ) {
        conferenceSelect.value =
            existing;
    } else {
        conferenceSelect.value =
            "ALL";
    }
}


function populateTeams() {

    const existing =
        teamSearchSelect.value;

    const teams =
        currentData
            .map(row => row.team)
            .filter(Boolean)
            .sort();


    teamSearchSelect.innerHTML =
        `<option value="">
            None
        </option>`;


    teams.forEach(
        team => {

            const option =
                document.createElement(
                    "option"
                );

            option.value = team;
            option.textContent = team;

            teamSearchSelect.appendChild(
                option
            );
        }
    );


    if (teams.includes(existing)) {
        teamSearchSelect.value =
            existing;
    }
}


function renderChart() {

    const metric =
        METRICS[
            metricSelect.value
        ];

    const conference =
        conferenceSelect.value;

    const highlightedTeam =
        teamSearchSelect.value;


    const benchmark =
        currentData.filter(
            row =>
                Number.isFinite(
                    Number(row[metric.x])
                )
                &&
                Number.isFinite(
                    Number(row[metric.y])
                )
        );


    const displayed =
        benchmark.filter(
            row =>
                conference === "ALL"
                ||
                row.conference === conference
        );


    const benchmarkX =
        average(
            benchmark.map(
                row =>
                    Number(row[metric.x])
            )
        );


    const benchmarkY =
        average(
            benchmark.map(
                row =>
                    Number(row[metric.y])
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


    const traces = [];


    traces.push({

        type: "scatter",

        mode: "markers+text",

        x:
            normalTeams.map(
                row =>
                    Number(row[metric.x])
            ),

        y:
            normalTeams.map(
                row =>
                    Number(row[metric.y])
            ),

        text:
            normalTeams.map(
                row => row.team
            ),

        textposition:
            "top center",

        customdata:
            normalTeams.map(
                row => [
                    row.team,
                    row.conference,
                    row.games
                ]
            ),

        marker: {
            size: 10,
            opacity: 0.70
        },

        hovertemplate:
            "<b>%{customdata[0]}</b>"
            + "<br>%{customdata[1]}"
            + "<br>Games: %{customdata[2]}"
            + `<br>${metric.xLabel}: %{x:${metric.format}}`
            + `<br>${metric.yLabel}: %{y:${metric.format}}`
            + "<extra></extra>",

        name:
            "FBS Teams"

    });


    if (highlighted.length) {

        traces.push({

            type: "scatter",

            mode: "markers+text",

            x:
                highlighted.map(
                    row =>
                        Number(row[metric.x])
                ),

            y:
                highlighted.map(
                    row =>
                        Number(row[metric.y])
                ),

            text:
                highlighted.map(
                    row => row.team
                ),

            textposition:
                "top center",

            marker: {
                size: 20,
                symbol: "star"
            },

            name:
                "Highlighted Team"

        });
    }


    const shapes = [];


    if (benchmarkX !== null) {

        shapes.push({
            type: "line",
            x0: benchmarkX,
            x1: benchmarkX,
            y0: 0,
            y1: 1,
            yref: "paper",
            line: {
                dash: "dash",
                width: 1
            }
        });
    }


    if (benchmarkY !== null) {

        shapes.push({
            type: "line",
            y0: benchmarkY,
            y1: benchmarkY,
            x0: 0,
            x1: 1,
            xref: "paper",
            line: {
                dash: "dash",
                width: 1
            }
        });
    }


    const season =
        seasonSelect.value;


    const layout = {

        title: {
            text:
                `${season} ${metric.title}`,
            x: 0.5
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
            title:
                metric.xLabel,
            zeroline:
                false
        },

        yaxis: {
            title:
                metric.yLabel,

            autorange:
                metric.reverseDefense
                    ? "reversed"
                    : true,

            zeroline:
                false
        },

        shapes:
            shapes,

        hovermode:
            "closest",

        showlegend:
            Boolean(
                highlightedTeam
            ),

        margin: {
            l: 85,
            r: 40,
            t: 80,
            b: 80
        }

    };


    const config = {

        responsive: true,

        displaylogo: false,

        modeBarButtonsToRemove: [
            "lasso2d",
            "select2d"
        ],

        toImageButtonOptions: {
            format: "png",
            filename:
                `btb-${season}-${metricSelect.value}`,
            scale: 2
        }

    };


    Plotly.react(
        "chart",
        traces,
        layout,
        config
    );


    const week =
        currentMeta.thru_week
        ??
        (
            currentData.length
                ? currentData[0].week
                : null
        );


    weekLabel.textContent =
        week !== null
            ? `Week ${week}`
            : season;


    teamCount.textContent =
        `${displayed.length} / ${benchmark.length} FBS`;
}


seasonSelect.addEventListener(
    "change",
    loadSeason
);

metricSelect.addEventListener(
    "change",
    renderChart
);

conferenceSelect.addEventListener(
    "change",
    renderChart
);

teamSearchSelect.addEventListener(
    "change",
    renderChart
);


loadSeason();
