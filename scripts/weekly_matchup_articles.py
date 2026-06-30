#!/usr/bin/env python3
"""
DROP-IN REWRITE for weekly_matchup_articles.py — narrative engine.

WHAT TO DO:
  1. Paste everything below into weekly_matchup_articles.py, replacing the
     existing `build_article` function (the long one near the bottom).
  2. Add the new helper functions ABOVE build_article.
  3. These functions are now UNUSED and can be deleted:
        build_offense_sentence, build_defense_sentence,
        build_special_teams_sentence, build_weather_sentence,
        build_model_prediction
     (build_article no longer calls them.)
  4. TWO small wiring edits in main(), to feed the proprietary model fields:
        a) after `model = load_model_data(...)`, add:
               model_rank_lookup = model_ranks(model)
        b) in the build_article(...) call, pass it as the new last arg:
               model_ranks_df=model_rank_lookup
     If you skip this, the article still renders — it just falls back to the
     nflverse EPA ranks and drops the QB callout / model-vs-market lead.
  5. No changes to compute_team_metrics, injury scraping, or the loaders.

Field directions used (verified against the model CSV):
  Offensive Expected Points / Success Rate -> higher better
  Defensive Expected Points -> LOWER better ; Defensive Success Rate -> higher
  QB EPA (career / last 10) -> higher better
nflverse positional ranks (off_pass_rank, def_rush_rank, ...) still feed the
Tale of the Tape and the secondary pass/rush storylines (rank 1 == best).
Model-side per-team values (Model Prediction, Qbname, ...) arrive already merged
into game_rows via your prepare_games() spreads<->model join.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# Plain-English translation: a rank becomes a word a normal bettor understands.
# ─────────────────────────────────────────────────────────────────────────────
def tier(rank: object, total_teams: int = 32) -> Optional[str]:
    """Map a 1-is-best rank to a tier phrase. Returns None if unavailable."""
    if rank is None or pd.isna(rank):
        return None
    r, n = int(rank), max(total_teams, 1)
    if r <= round(n * 0.12):
        return "elite"
    if r <= round(n * 0.31):
        return "one of the league's better"
    if r <= round(n * 0.47):
        return "above-average"
    if r <= round(n * 0.56):
        return "middling"
    if r <= round(n * 0.75):
        return "below-average"
    if r <= round(n * 0.90):
        return "struggling"
    return "among the league's worst"


def _safe_rank(metrics: pd.Series, col: str) -> Optional[int]:
    if metrics is None or not hasattr(metrics, "get"):
        return None
    val = metrics.get(col)
    if val is None or pd.isna(val):
        return None
    return int(val)


def _pick(variants: List[str], seed: str) -> str:
    """Deterministic phrasing rotation so 16 articles don't share a sentence.
    NOTE: if any rendered line shows up on >30% of a week's games, add variants."""
    if not variants:
        return ""
    return variants[hash(seed) % len(variants)]


def poss(name: str) -> str:
    """Possessive that handles names ending in s: Chargers -> Chargers'."""
    return name + "'" if str(name).endswith("s") else name + "'s"


# ─────────────────────────────────────────────────────────────────────────────
# MODEL-FIELD layer: rank the proprietary columns across the 32-team slate.
# Directions verified from the data:
#   Offensive Expected Points / Success Rate -> higher is better
#   Defensive Expected Points -> LOWER is better ; Defensive Success Rate -> higher
#   QB EPA (career / last 10) -> higher is better
# ─────────────────────────────────────────────────────────────────────────────
def model_ranks(model_df: pd.DataFrame) -> pd.DataFrame:
    """League-wide ranks + raw values from the model CSV, indexed by team abbr.

    Pass the FULL model frame (all 32 teams) so ranks are slate-wide. Build this
    once in main() and hand it to build_article.
    """
    df = model_df.copy()
    df.columns = [c.strip() for c in df.columns]

    def rk(col: str, higher_better: bool) -> pd.Series:
        if col not in df.columns:
            return pd.Series([pd.NA] * len(df), index=df.index, dtype="Int64")
        return df[col].rank(method="min", ascending=not higher_better).astype("Int64")

    out = pd.DataFrame({"team": df["Team"].astype(str).str.upper()})
    out["off_rank"] = rk("Offensive Expected Points (Season)", True)
    out["def_rank"] = rk("Defensive Expected Points (Season)", False)   # lower = better D
    out["off_sr_rank"] = rk("Offensive Success Rate (%)", True)
    out["def_sr_rank"] = rk("Defensive Success Rate (%)", True)
    out["qb10_rank"] = rk("QB Expected Points Added (Last 10 games)", True)
    # raw values for display
    for src, dst in [
        ("Model Prediction", "model_pred"),
        ("QB Expected Points Added (Last 10 games)", "qb10"),
        ("QB Expected Points Added (Career)", "qb_career"),
        ("Offensive Eckel Rate Over Expected (%)", "off_eckel"),
        ("Defensive Eckel Rate Over Expected (%)", "def_eckel"),
        ("Qbname", "qbname"),
        ("PROE", "proe"),
    ]:
        out[dst] = df[src].values if src in df.columns else pd.NA
    return out.set_index("team")


def clean_qb(value: object) -> Optional[str]:
    """'J.Allen' -> 'J. Allen'. Returns None if missing.
    If you have the fuller-name lookup from your odds page, map it in here."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    return re.sub(r"^([A-Za-z])\.([A-Za-z])", r"\1. \2", s)


def _spread_words(x: object) -> Optional[str]:
    if x is None or pd.isna(x):
        return None
    v = float(x)
    if v < 0:
        return f"a {abs(v):.1f}-point favorite"
    if v > 0:
        return f"a {abs(v):.1f}-point underdog"
    return "a pick'em"


def model_vs_market_lead(bet_name, model_pred, market_line, seed) -> Optional[str]:
    """The most digestible edge framing: our projected line vs the market's."""
    words = _spread_words(model_pred)
    if words is None or market_line is None or pd.isna(market_line):
        return None
    gap = abs(float(model_pred) - float(market_line))
    market_words = _spread_words(market_line) or format_line(market_line)
    return _pick(
        [
            f"Our model makes **{bet_name}** {words}. The market is only pricing them "
            f"as {market_words} — a **{gap:.1f}-point** gap, and that gap is the bet.",
            f"The number doing the work: our model projects **{bet_name}** {words}, "
            f"while the market sits at {market_words}. That **{gap:.1f} points** of "
            f"disagreement is the edge.",
        ],
        seed,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Salience mismatch engine: find the ONE storyline the data is telling.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class UnitBattle:
    dimension: str   # 'overall' | 'pass' | 'rush' | 'pressure'
    off_team: str
    def_team: str
    off_rank: int
    def_rank: int
    score: float     # >0 => offense exploits a weak defense; <0 => defense wins


_DIMS = [
    ("overall", "off_rank", "def_rank"),          # MODEL Expected Points ranks
    ("pass", "off_pass_rank", "def_pass_rank"),    # nflverse, positional color
    ("rush", "off_rush_rank", "def_rush_rank"),    # nflverse, positional color
]


def _battles(off_name, off_m, def_name, def_m, total_teams) -> List[UnitBattle]:
    mid = (total_teams + 1) / 2.0
    out: List[UnitBattle] = []
    for dim, ocol, dcol in _DIMS:
        orank, drank = _safe_rank(off_m, ocol), _safe_rank(def_m, dcol)
        if orank is None or drank is None:
            continue
        # off_strength: good offense is low rank -> positive
        # def_weak:     weak defense is high rank -> positive
        score = (mid - orank) + (drank - mid)
        out.append(UnitBattle(dim, off_name, def_name, orank, drank, score))
    return out


def _overall(battles: List[UnitBattle]) -> Optional[UnitBattle]:
    for b in battles:
        if b.dimension == "overall":
            return b
    return None


def pick_support_and_risk(
    bet_name, bet_m, opp_name, opp_m, total_teams, risk_floor: float = 2.0
) -> Tuple[Optional[Tuple[str, UnitBattle]], Optional[Tuple[str, UnitBattle]]]:
    """Return (support, risk).

    SUPPORT is anchored on OVERALL efficiency (what the model actually prices),
    framed as offense-led or defense-led depending on which favors the bet — so
    the 'Why' can never argue against the pick, and never leads with a freak
    positional number on a unit the team rarely uses.

    RISK is the opponent's genuine path to covering:
      ('exploit', battle)    -> a real edge the opponent can attack (score >= floor)
      ('outclassed', battle) -> no real edge; narrate their best unit + variance
    """
    bet_off = _battles(bet_name, bet_m, opp_name, opp_m, total_teams)   # bet has ball
    opp_off = _battles(opp_name, opp_m, bet_name, bet_m, total_teams)   # opp has ball

    # ── Support: overall efficiency, offense-led vs defense-led ───────────────
    support = None
    cands = []
    bo, oo = _overall(bet_off), _overall(opp_off)
    if bo is not None:
        cands.append(("offense", bo, bo.score))     # bet offense vs opp defense
    if oo is not None:
        cands.append(("defense", oo, -oo.score))    # bet defense vs opp offense
    if cands:
        cands.sort(key=lambda x: x[2], reverse=True)
        support = (cands[0][0], cands[0][1])

    # ── Risk: opponent's real avenue, or honest "outclassed" fallback ─────────
    risk = None
    if opp_off:
        best = max(opp_off, key=lambda b: b.score)
        if best.score >= risk_floor:
            risk = ("exploit", best)               # opp can genuinely attack here
        else:
            usable = [b for b in opp_off if b.dimension in ("overall", "pass", "rush")]
            if usable:
                risk = ("outclassed", min(usable, key=lambda b: b.off_rank))
    return support, risk


# ─────────────────────────────────────────────────────────────────────────────
# Render a storyline into a sentence (translated + rotated, never raw EPA).
# ─────────────────────────────────────────────────────────────────────────────
def _ord(n: int) -> str:  # local ordinal so this module is self-contained
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def render_storyline(role: str, b: UnitBattle, total_teams: int, seed: str) -> str:
    """role='offense' -> the offense exploits; role='defense' -> the defense wins."""
    o, d = b.off_team, b.def_team
    o_ord, d_ord = _ord(b.off_rank), _ord(b.def_rank)
    o_tier, d_tier = tier(b.off_rank, total_teams), tier(b.def_rank, total_teams)

    if role == "offense":
        templates = {
            "overall": [
                f"{poss(o)} offense ({o_tier}, {o_ord} in efficiency) draws a get-right "
                f"spot against {poss(d)} {d_tier} defense ({d_ord}).",
                f"The unit to attack is {poss(d)} defense ({d_ord}, {d_tier}), and {o} "
                f"({o_ord} on offense) is equipped to do it.",
                f"On efficiency alone {o} ({o_ord}) should move the ball on {d} ({d_ord}).",
            ],
            "pass": [
                f"{o} can throw on this {d} secondary — the {o_ord}-ranked passing "
                f"attack against a pass defense sitting {d_ord}.",
                f"The matchup to exploit is {poss(o)} pass game ({o_ord}) versus {poss(d)} "
                f"{d_tier} pass defense ({d_ord}).",
            ],
            "rush": [
                f"{poss(o)} ground game ({o_ord}) lines up against a {d} front giving up "
                f"yards on the ground ({d_ord} in rush defense).",
                f"{o} can lean on the run here — {o_ord} rushing offense into {poss(d)} "
                f"{d_tier} run defense ({d_ord}).",
            ],
            "pressure": [
                f"{poss(o)} line should keep it clean ({o_ord} in sacks allowed) against "
                f"a {d} pass rush that hasn't gotten home ({d_ord}).",
            ],
        }
    else:  # defense wins -> subject is the DEFENSE team (b.def_team)
        templates = {
            "overall": [
                f"{poss(d)} defense ({d_tier}, {d_ord}) should smother {poss(o)} {o_tier} "
                f"offense ({o_ord}) — that gap is the spine of the number.",
                f"This rides on {poss(d)} defense ({d_ord}, {d_tier}) against {o}, "
                f"whose offense ranks just {o_ord}.",
                f"{poss(o)} offense ({o_ord}) runs into {poss(d)} {d_tier} defense ({d_ord}), "
                f"and the model trusts the defense.",
            ],
            "pass": [
                f"{poss(d)} pass defense ({d_ord}, {d_tier}) is built to take away {poss(o)} "
                f"{o_ord}-ranked passing game.",
            ],
            "rush": [
                f"{poss(d)} run defense ({d_ord}) should bottle up {poss(o)} {o_ord} ground game "
                f"and keep them one-dimensional.",
            ],
            "pressure": [
                f"{poss(d)} pass rush ({d_ord} in sacks) against shaky {o} protection "
                f"({o_ord} in sacks allowed) is a script-wrecker.",
            ],
        }
    options = templates.get(b.dimension) or templates["overall"]
    return _pick(options, seed + role + b.dimension)


def headline_tail(support: Optional[Tuple[str, UnitBattle]], bet_name: str) -> str:
    if not support:
        return "a model lean"
    role, b = support
    if role == "defense":
        return f"betting {poss(bet_name)} defense"
    return {
        "pass": f"value in {poss(bet_name)} passing game",
        "rush": f"value on the ground for {bet_name}",
        "pressure": f"{poss(bet_name)} edge up front",
    }.get(b.dimension, f"a model edge on {bet_name}")


def render_risk(risk: Tuple[str, UnitBattle], opp_name: str,
                total_teams: int, seed: str) -> str:
    """Render the risk: a real exploit, or an honest 'outclassed + variance' line."""
    kind, b = risk
    if kind == "exploit":
        return render_storyline("offense", b, total_teams, seed)  # opp offense exploits
    o_ord = _ord(b.off_rank)
    unit = {"overall": "on offense", "pass": "through the air",
            "rush": "on the ground"}.get(b.dimension, "on offense")
    return _pick(
        [
            f"{poss(opp_name)} best path is {unit} ({o_ord}), but they're outgunned across "
            f"the board — so the real threat is variance: a fluky turnover, a "
            f"special-teams swing, or garbage-time points backing them into a cover.",
            f"There's no obvious soft spot for {opp_name} to attack; their {o_ord}-ranked "
            f"offense is the only lever. Realistically the risk is variance — short "
            f"fields, a non-offensive score, or a late backdoor cover.",
        ],
        seed,
    )


def assumed_starters(bet_name, bet_m, opp_name, opp_m) -> Optional[str]:
    """Trust line: name the QBs the model assumes, so readers can sanity-check
    against injury news before betting. NFL QB attrition is high — this matters."""
    a, b = clean_qb(bet_m.get("qbname")), clean_qb(opp_m.get("qbname"))
    if not a and not b:
        return None
    parts = []
    if a:
        parts.append(f"{a} ({bet_name})")
    if b:
        parts.append(f"{b} ({opp_name})")
    return (
        f"*Model assumes {' and '.join(parts)} under center. QB news moves these "
        f"numbers fast — check inactives before you bet.*"
    )


def qb_xfactor(bet_name, bet_m, opp_name, opp_m, total_teams, seed) -> List[str]:
    """A named QB callout, fired only when a starter's last-10 EPA is extreme.
    Replaces the old special-teams filler. Names build trust and engagement."""
    out: List[str] = []
    for team_name, mm in [(bet_name, bet_m), (opp_name, opp_m)]:
        name = clean_qb(mm.get("qbname"))
        rank = _safe_rank(mm, "qb10_rank")
        if not name or rank is None:
            continue
        if rank <= 5:
            out.append(
                _pick(
                    [f"{name} has been one of the most valuable quarterbacks in "
                     f"football over his last 10 games ({_ord(rank)} of {total_teams}) "
                     f"— a real tailwind for {team_name}.",
                     f"{name} grades out {_ord(rank)} of {total_teams} in QB value over "
                     f"his last 10 starts; that's the engine behind {poss(team_name)} "
                     f"number."],
                    seed + team_name,
                )
            )
        elif rank >= total_teams - 4:
            out.append(
                _pick(
                    [f"{name} sits {_ord(rank)} of {total_teams} in QB value over his "
                     f"last 10 games — the kind of play that caps {poss(team_name)} "
                     f"ceiling.",
                     f"{name} has been among the least productive starters in the league "
                     f"lately ({_ord(rank)} of {total_teams}), a real drag on {team_name}."],
                    seed + team_name,
                )
            )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Section builders
# ─────────────────────────────────────────────────────────────────────────────
def _side_facts(row: pd.Series) -> Dict[str, object]:
    cover = row.get("best_cover_probability")
    if cover is None or pd.isna(cover):
        cover = row.get("model_cover_probability")
    edge = row.get("best_edge")
    if edge is None or pd.isna(edge):
        edge = row.get("edge_numeric")
    return {"cover": cover, "edge": edge, "line": row.get("best_line"),
            "price": row.get("best_price")}


def build_bottom_line(
    bet_name, bet_line, confidence, bet_facts, opp_name, opp_line, opp_facts,
    seed, has_bet, model_lead=None,
) -> List[str]:
    """Human paragraph + a scannable two-row table. Replaces the Q&A 'Verdict'.
    `model_lead` (model-line-vs-market sentence) leads when available."""
    out: List[str] = ["## The Bottom Line"]
    if has_bet:
        hammer = (
            "a lean, not a hammer" if confidence == "Lean"
            else "a confident play" if confidence == "Strong" else "a lean"
        )
        if model_lead:
            out.append(model_lead)
            out.append(
                f"That puts **{bet_name} {bet_line}** on the card at "
                f"{_price(bet_facts['price'])} — {hammer}."
            )
        else:
            out.append(
                _pick(
                    [f"The play is **{bet_name} {bet_line}**. At "
                     f"{display_percent(bet_facts['cover'], 1)} to cover, the price "
                     f"({_price(bet_facts['price'])}) leaves a "
                     f"{display_edge(bet_facts['edge'])} edge on the table. Treat it as "
                     f"{hammer}."],
                    seed,
                )
            )
    else:
        out.append(
            f"**No play here.** Neither side clears our 4% edge bar, so we're passing "
            f"— and that discipline is the point. The closest look is {bet_name} "
            f"{bet_line} at {display_edge(bet_facts['edge'])}, still short of the trigger."
        )
    out.append("")
    out.append("| | Model cover % | Edge | Call |")
    out.append("|---|---|---|---|")
    call_bet = "✅ " + confidence if has_bet else "below 4%"
    out.append(
        f"| **{bet_name} {bet_line}** | {display_percent(bet_facts['cover'], 1)} "
        f"| {display_edge(bet_facts['edge'])} | {call_bet} |"
    )
    out.append(
        f"| {opp_name} {opp_line} | {display_percent(opp_facts['cover'], 1)} "
        f"| {display_edge(opp_facts['edge'])} | below 4% |"
    )
    return out


def build_tale_of_tape(bet_name, bet_m, opp_name, opp_m, total_teams) -> List[str]:
    """Numbers belong in a table, not the prose. Lead with the model's own fields."""
    def cell_rank(mm, col):
        r = _safe_rank(mm, col)
        return _ord(r) if r else "—"

    def cell_pct(mm, col):
        v = mm.get(col) if hasattr(mm, "get") else None
        return display_percent(v, 1) if v is not None and not pd.isna(v) else "—"

    def cell_qb(mm):
        name = clean_qb(mm.get("qbname"))
        r = _safe_rank(mm, "qb10_rank")
        if name and r:
            return f"{name} ({_ord(r)})"
        return name or "—"

    rows = [
        ("QB, last 10 (EPA rank)", lambda mm: cell_qb(mm)),
        ("Offense (success rate)", lambda mm: cell_rank(mm, "off_sr_rank")),
        ("Defense (success rate)", lambda mm: cell_rank(mm, "def_sr_rank")),
        ("Offensive Eckel ROE", lambda mm: cell_pct(mm, "off_eckel")),
        ("Pass offense", lambda mm: cell_rank(mm, "off_pass_rank")),
        ("Rush defense", lambda mm: cell_rank(mm, "def_rush_rank")),
    ]
    body = []
    for label, fn in rows:
        bcell, ocell = fn(bet_m), fn(opp_m)
        if bcell == "—" and ocell == "—":
            continue
        body.append(f"| {label} | {bcell} | {ocell} |")
    if not body:
        return []
    return (
        ["## Tale of the Tape", "", f"| | {bet_name} | {opp_name} |", "|---|---|---|"]
        + body
    )


def _price(value: object) -> str:
    if value is None or pd.isna(value):
        return "-110"
    f = float(value)
    return f"{f:+.0f}" if f > 0 else f"{f:.0f}"


def build_cta(edge_game_count: int, has_bet: bool) -> List[str]:
    if has_bet:
        hook = (
            f"**{edge_game_count} games clear our 4% threshold this week — this is one "
            f"of them.** See all {edge_game_count}, live and updating as the lines move,"
        )
    else:
        hook = (
            f"**This isn't one of our plays — but {edge_game_count} games clear our 4% "
            f"threshold this week.** See every one of them, live,"
        )
    return [
        "---",
        f"{hook} in the member dashboard → "
        "[btb-analytics.com/member-access](https://btb-analytics.com/member-access)",
        "",
        "_Built by the BTB model. We target a 55–57% win rate and publish every "
        "result, wins and losses. [How the model works] · [Our full record]_",
    ]


# ─────────────────────────────────────────────────────────────────────────────
# REPLACEMENT build_article — assembles the new, narrative structure.
# ─────────────────────────────────────────────────────────────────────────────
def build_article(
    game, game_rows, metrics, records, team_names, schedule_row,
    stat_context, provenance, injury_reports, edge_game_count, model_ranks_df=None,
) -> Tuple[str, Dict[str, object]]:
    away_team, home_team = game.split("@")
    rows_by_team = {row["team"]: row for _, row in game_rows.iterrows()}
    away_row, home_row = rows_by_team[away_team], rows_by_team[home_team]
    metrics_indexed = metrics.set_index("team")
    mr = model_ranks_df  # league-wide model ranks/values, indexed by team
    total_teams = (
        int(len(mr)) if mr is not None and len(mr)
        else (int(metrics["team"].nunique()) if not metrics.empty else 32)
    )

    def m(team):
        """Per-team metrics: nflverse positional ranks + model ranks/values merged.
        Aliases off_rank/def_rank to nflverse EPA ranks if model data is absent."""
        base = metrics_indexed.loc[team] if team in metrics_indexed.index else pd.Series(dtype="float64")
        if mr is not None and team in mr.index:
            base = pd.concat([base, mr.loc[team]])
        if "off_rank" not in base.index and "off_epa_rank" in base.index:
            base["off_rank"] = base.get("off_epa_rank")
            base["def_rank"] = base.get("def_epa_rank")
        return base

    away_name = team_names.get(away_team, away_team)
    home_name = team_names.get(home_team, home_team)

    kickoff = away_row["game_date_est"]
    kickoff_label = kickoff.strftime("%Y-%m-%d") if pd.notna(kickoff) else "N/A"
    time_label = away_row.get("game_time_est", "N/A")
    location = None
    if schedule_row is not None:
        stadium = schedule_row.get("stadium")
        if isinstance(stadium, str) and stadium.strip():
            location = f"{stadium} ({home_name})"

    # favorite / underdog by market line (most negative == favorite)
    favorite_row = game_rows.sort_values("market_line").iloc[0]
    dog_row = game_rows.sort_values("market_line").iloc[-1]

    # verdict side = larger edge
    fav_edge = resolve_edge_numeric(favorite_row)
    dog_edge = resolve_edge_numeric(dog_row)
    verdict_row = (
        favorite_row
        if fav_edge is not None and (dog_edge is None or fav_edge >= dog_edge)
        else dog_row
    )
    other_row = dog_row if verdict_row is favorite_row else favorite_row
    verdict_edge = resolve_edge_numeric(verdict_row)
    confidence = edge_confidence_label(verdict_edge)
    has_bet = verdict_edge is not None and verdict_edge >= 0.04

    bet_team = verdict_row["team"]
    opp_team = other_row["team"]
    bet_name = team_names.get(bet_team, bet_team)
    opp_name = team_names.get(opp_team, opp_team)
    bet_line = format_line(verdict_row.get("best_line"))
    opp_line = format_line(other_row.get("best_line"))
    bet_m, opp_m = m(bet_team), m(opp_team)
    seed = game  # stable per-matchup seed for phrasing rotation

    support, risk = pick_support_and_risk(bet_name, bet_m, opp_name, opp_m, total_teams)

    lines_summary = (
        f"{favorite_row['team']} {format_float(favorite_row['market_line'])} / "
        f"{dog_row['team']} +{format_float(abs(dog_row['market_line']))}"
    )
    best_book_summary = (
        f"{favorite_row['team']} {format_float(favorite_row['best_line'])} at "
        f"{favorite_row['best_book']}; {dog_row['team']} "
        f"+{format_float(abs(dog_row['best_line']))} at {dog_row['best_book']}"
    )

    # ── Assemble ──────────────────────────────────────────────────────────────
    sections: List[str] = []

    away_logo, home_logo = extract_team_logo(away_row), extract_team_logo(home_row)
    if away_logo or home_logo:
        parts = []
        if away_logo:
            parts.append(f"![{away_name}]({away_logo})")
        if home_logo:
            parts.append(f"![{home_name}]({home_logo})")
        sections.extend(["  ".join(parts), ""])

    if has_bet:
        sections.append(f"# {bet_name} {bet_line} vs. {opp_name}: {headline_tail(support, bet_name)}")
    else:
        sections.append(f"# {away_name} at {home_name}: a game we're passing on")
    sections.append("")

    bet_facts, opp_facts = _side_facts(verdict_row), _side_facts(other_row)
    model_lead = model_vs_market_lead(
        bet_name, verdict_row.get("Model Prediction"),
        verdict_row.get("best_line", verdict_row.get("market_line")), seed,
    ) if has_bet else None
    sections.extend(
        build_bottom_line(bet_name, bet_line, confidence, bet_facts,
                          opp_name, opp_line, opp_facts, seed, has_bet, model_lead)
    )

    # Assumed starters — trust line so readers can sanity-check QB news
    starters_note = assumed_starters(bet_name, bet_m, opp_name, opp_m)
    if starters_note:
        sections.extend(["", starters_note])

    # Line + movement, one tight sentence (no boilerplate)
    fav_open, fav_now = favorite_row.get("market_line"), favorite_row.get("best_line")
    move = ""
    if pd.notna(fav_open) and pd.notna(fav_now):
        if abs(float(fav_now) - float(fav_open)) < 0.5:
            move = "The market hasn't moved off the open."
        else:
            toward = dog_row["team"] if float(fav_now) > float(fav_open) else favorite_row["team"]
            move = f"The number has moved toward {team_names.get(toward, toward)}."
    sections.extend(["", f"*Line: opened {lines_summary}. Best now: {best_book_summary}. {move}*"])

    # Why — always supports the pick
    if has_bet and support:
        sections.extend(["", "## Why the Pick",
                         render_storyline(support[0], support[1], total_teams, seed)])
        # one supporting clause about the slimness of a lean, if applicable
        if confidence == "Lean":
            sections.append(
                _pick(
                    ["The edge is real but slim, so this is a confidence-tier lean, "
                     "not a number to overload.",
                     "It clears the bar without much room to spare — bet it, but size "
                     "it like the lean it is."],
                    seed,
                )
            )
    elif not has_bet and support:
        sections.extend(["", "## What the Model Sees",
                         render_storyline(support[0], support[1], total_teams, seed),
                         "The lean exists — it just isn't big enough to bet."])

    # Risk — the opponent's real path, or an honest variance read
    if risk:
        sections.extend(["", "## The Risk",
                         render_risk(risk, opp_name, total_teams, seed)])
        if has_bet and risk[0] == "exploit":
            sections.append(
                _pick(
                    ["If that shows up, the cover gets live late and a slim edge "
                     "doesn't survive much going wrong.",
                     "That's the path that busts this ticket — watch for it early."],
                    seed,
                )
            )

    # QB X-factor — named callout when a starter's last-10 EPA is extreme
    qb_lines = qb_xfactor(bet_name, bet_m, opp_name, opp_m, total_teams, seed)
    if qb_lines:
        sections.extend(["", "## Quarterback X-Factor"] + qb_lines)

    # Tale of the tape — numbers in a table, not the prose
    tape = build_tale_of_tape(bet_name, bet_m, opp_name, opp_m, total_teams)
    if tape:
        sections.extend([""] + tape)

    # Injury report — kept, conditional, no null leakage
    injury_lines = []
    for team, team_name in [(away_team, away_name), (home_team, home_name)]:
        report = injury_reports[team]
        if report.starters:
            entries = [
                f"{inj.player} ({inj.status or 'status unavailable'})"
                for inj in report.starters
            ]
            injury_lines.append(f"**{team_name}:** " + "; ".join(entries) + ".")
    if injury_lines:
        sections.extend(["", "## Injury Report"] + injury_lines)

    sections.extend([""] + build_cta(edge_game_count, has_bet))

    payload = {
        "game": game, "away_team": away_team, "home_team": home_team,
        "kickoff_date": kickoff_label, "kickoff_time_et": time_label,
        "location": location, "bet_side": f"{bet_name} {bet_line}" if has_bet else None,
        "confidence": confidence, "has_bet": has_bet,
        "governing_storyline": (support[0] + ":" + support[1].dimension) if support else None,
        "line_summary": lines_summary, "best_book_summary": best_book_summary,
        "stat_context": asdict(stat_context),
        "injury_reports": {
            t: {"status": r.status, "starters": [asdict(i) for i in r.starters]}
            for t, r in injury_reports.items()
        },
    }
    return "\n".join(sections) + "\n", payload
