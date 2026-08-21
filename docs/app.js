const seasonSelect = document.getElementById("season");
const metricSelect = document.getElementById("metric");
const conferenceSelect = document.getElementById("conference");
const teamSearchSelect = document.getElementById("team-search");

const weekLabel = document.getElementById("week-label");
const teamCount = document.getElementById("team-count");

let currentData = [];
let currentMeta = {};


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


function hasValidPair(row, metric) {

    const x =
        Number(
            row[
                metric.x
            ]
        );

    const y =
        Number(
            row[
                metric.y
            ]
        );

    return (
        Number.isFinite(x)
        &&
        Number.isFinite(y)
    );
}


async function loadSeason() {

    const season =
        seasonSelect.value;

    const dataPath =
        `./data/${season}/latest.json`;

    const metaPath =
        `./data/${season}/meta.json`;

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
                `Unable to load ${dataPath}`
            );
        }

        currentData =
            await response.json();


        if (
            !Array.isArray(currentData)
        ) {

            throw new Error(
                `${dataPath} did not contain a JSON array`
            );
        }


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


        populateConferences();
        populateTeams();
        updateMetricAvailability();
        renderChart();

    } catch (error) {

        console.error(error);

        currentData = [];
        currentMeta = {};

        conferenceSelect.innerHTML =
            `
            <option value="ALL">
                All FBS
            </option>
            `;

        teamSearchSelect.innerHTML =
            `
            <option value="">
                None
            </option>
            `;

        weekLabel.textContent =
            "Unavailable";

        teamCount.textContent =
            "0";

        document.getElementById(
            "chart"
        ).innerHTML =
            `
            <div style="
                color:#111;
                padding:40px;
                font-family:Arial;
            ">
                Data for ${season} is not currently available.
                <br><br>
                ${error.message}
            </div>
            `;
    }
}


function populateConferences() {

    const existing =
        conferenceSelect.value;

    const conferences =
        [
            ...new Set(
                currentData
                    .map(
                        row =>
                            row.conference
                    )
                    .filter(Boolean)
            )
        ].sort();


    conferenceSelect.innerHTML =
        `
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

            conferenceSelect.appendChild(
                option
            );
        }
    );


    if (
        conferences.includes(
            existing
        )
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
        [
            ...new Set(
                currentData
                    .map(
                        row =>
                            row.team
                    )
                    .filter(Boolean)
            )
        ].sort();


    teamSearchSelect.innerHTML =
        `
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

            teamSearchSelect.appendChild(
                option
            );
        }
    );


    if (
        teams.includes(
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
            metricKey,
            metric
        ]
        of Object.entries(
            METRICS
        )
    ) {

        availability[
            metricKey
        ] =
            currentData.some(
                row =>
                    row[
                        metric.x
                    ] !== null
                    &&
                    row[
                        metric.x
                    ] !== undefined
                    &&
                    row[
                        metric.y
                    ] !== null
                    &&
                    row[
                        metric.y
                    ] !== undefined
                    &&
                    Number.isFinite(
                        Number(
                            row[
                                metric.x
                            ]
                        )
                    )
                    &&
                    Number.isFinite(
                        Number(
                            row[
                                metric.y
                            ]
                        )
                    )
            );
    }


    for (
        const option
        of metricSelect.options
    ) {

        const isAvailable =
            availability[
                option.value
            ] === true;

        option.disabled =
            !isAvailable;
    }


    const selectedOption =
        metricSelect.options[
            metricSelect.selectedIndex
        ];


    if (
        !selectedOption
        ||
        selectedOption.disabled
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
                    availability[
                        key
                    ]
            );

        if (
            firstAvailable
        ) {

            metricSelect.value =
                firstAvailable;
        }
    }
}


function renderChart() {

    const metricKey =
        metricSelect.value;

    const metric =
        METRICS[
            metricKey
        ];


    if (!metric) {

        document.getElementById(
            "chart"
        ).innerHTML =
            `
            <div style="
                color:#111;
                padding:40px;
                font-family:Arial;
            ">
                No valid metric is selected.
            </div>
            `;

        return;
    }


    const conference =
        conferenceSelect.value;

    const highlightedTeam =
        teamSearchSelect.value;


    /*
     * Benchmark data always uses all FBS teams.
     *
     * This means conference filtering changes only which
     * teams are displayed. The average divider lines remain
     * based on the full FBS population.
     */
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
                conference === "ALL"
                ||
                row.conference === conference
        );


    if (!benchmark.length) {

        Plotly.purge(
            "chart"
        );

        document.getElementById(
            "chart"
        ).innerHTML =
            `
            <div style="
                color:#111;
                padding:40px;
                font-family:Arial;
            ">
                ${metric.title} data is not yet available
                for the selected season.
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
                row.team
                !== highlightedTeam
        );


    const highlighted =
        displayed.filter(
            row =>
                row.team
                === highlightedTeam
        );


    const traces = [];


    traces.push({

        type:
            "scatter",

        mode:
            "markers+text",

        x:
            normalTeams.map(
                row =>
                    Number(
                        row[
                            metric.x
                        ]
                    )
            ),

        y:
            normalTeams.map(
                row =>
                    Number(
                        row[
                            metric.y
                        ]
                    )
            ),

        text:
            normalTeams.map(
                row =>
                    row.team
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
            + "<br>Conference: %{customdata[1]}"
            + "<br>Games: %{customdata[2]}"
            + `<br>${metric.xLabel}: %{x:${metric.format}}`
            + `<br>${metric.yLabel}: %{y:${metric.format}}`
            + "<extra></extra>",

        name:
            "FBS Teams"

    });


    if (
        highlighted.length
    ) {

        traces.push({

            type:
                "scatter",

            mode:
                "markers+text",

            x:
                highlighted.map(
                    row =>
                        Number(
                            row[
                                metric.x
                            ]
                        )
                ),

            y:
                highlighted.map(
                    row =>
                        Number(
                            row[
                                metric.y
                            ]
                        )
                ),

            text:
                highlighted.map(
                    row =>
                        row.team
                ),

            textposition:
                "top center",

            customdata:
                highlighted.map(
                    row => [
                        row.team,
                        row.conference,
                        row.games
                    ]
                ),

            marker: {
                size: 20,
                symbol: "star"
            },

            hovertemplate:
                "<b>%{customdata[0]}</b>"
                + "<br>Conference: %{customdata[1]}"
                + "<br>Games: %{customdata[2]}"
                + `<br>${metric.xLabel}: %{x:${metric.format}}`
                + `<br>${metric.yLabel}: %{y:${metric.format}}`
                + "<extra></extra>",

            name:
                "Highlighted Team"

        });
    }


    const shapes = [];


    if (
        benchmarkX !== null
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
        benchmarkY !== null
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

        hovermode:
            "closest",

        showlegend:
            Boolean(
                highlightedTeam
            ),

        margin: {
            l:
                90,
            r:
                40,
            t:
                80,
            b:
                90
        },

        autosize:
            true
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


function updateWeekLabel() {

    const season =
        seasonSelect.value;


    const week =
        currentMeta.thru_week
        ??
        (
            currentData.length
                ? currentData[0].week
                : null
        );


    if (
        Number(week) === 0
    ) {

        weekLabel.textContent =
            `${season} Preseason`;

        return;
    }


    if (
        week !== null
        &&
        week !== undefined
    ) {

        weekLabel.textContent =
            `Week ${week}`;

        return;
    }


    weekLabel.textContent =
        season;
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


/*
 * Load the default season immediately.
 */
loadSeason();
