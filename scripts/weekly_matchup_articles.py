diff --git a/scripts/weekly_matchup_articles.py b/scripts/weekly_matchup_articles.py
index 77a752d..PATCHED 100644
--- a/scripts/weekly_matchup_articles.py
+++ b/scripts/weekly_matchup_articles.py
@@
 COMBINED_DICTIONARY_PATH = (
     Path(__file__).resolve().parents[1] / "inst" / "extdata" / "combined_data_dictionary.csv"
 )
+QB_CROSSWALK_PATH = (
+    Path(__file__).resolve().parents[1] / "data-raw" / "QB Crosswalk.csv"
+)
@@
 def read_repo_csv(
@@
     )
     return pd.read_csv(StringIO(text))
+
+
+def load_qb_crosswalk() -> Dict[str, str]:
+    """Load QB short-name -> full-name mapping from data-raw/QB Crosswalk.csv."""
+    if not QB_CROSSWALK_PATH.exists():
+        return {}
+    crosswalk = pd.read_csv(QB_CROSSWALK_PATH)
+    short_col = "starter_player_name"
+    full_col = "Full Name"
+    if short_col not in crosswalk.columns or full_col not in crosswalk.columns:
+        return {}
+    valid = crosswalk[[short_col, full_col]].dropna()
+    valid[short_col] = valid[short_col].astype(str).str.strip()
+    valid[full_col] = valid[full_col].astype(str).str.strip()
+    return dict(zip(valid[short_col], valid[full_col]))
@@
-def clean_qb(value: object) -> Optional[str]:
-    """'J.Allen' -> 'J. Allen'. Returns None if missing.
-    If you have the fuller-name lookup from your odds page, map it in here."""
+def clean_qb(value: object, qb_crosswalk: Optional[Dict[str, str]] = None) -> Optional[str]:
+    """Normalize QB display name.
+
+    - If value is in crosswalk (e.g., 'J.Allen'), return full name ('Josh Allen').
+    - Else fallback to spacing normalization ('J.Allen' -> 'J. Allen').
+    """
     if value is None or (isinstance(value, float) and pd.isna(value)):
         return None
     s = str(value).strip()
     if not s or s.lower() == "nan":
         return None
+    if qb_crosswalk:
+        mapped = qb_crosswalk.get(s)
+        if mapped:
+            return mapped
     return re.sub(r"^([A-Za-z])\.([A-Za-z])", r"\1. \2", s)
@@
-def assumed_starters(bet_name, bet_m, opp_name, opp_m) -> Optional[str]:
+def assumed_starters(bet_name, bet_m, opp_name, opp_m, qb_crosswalk=None) -> Optional[str]:
@@
-    a, b = clean_qb(bet_m.get("qbname")), clean_qb(opp_m.get("qbname"))
+    a = clean_qb(bet_m.get("qbname"), qb_crosswalk)
+    b = clean_qb(opp_m.get("qbname"), qb_crosswalk)
@@
-def qb_xfactor(bet_name, bet_m, opp_name, opp_m, total_teams, seed) -> List[str]:
+def qb_xfactor(bet_name, bet_m, opp_name, opp_m, total_teams, seed, qb_crosswalk=None) -> List[str]:
@@
-        name = clean_qb(mm.get("qbname"))
+        name = clean_qb(mm.get("qbname"), qb_crosswalk)
@@
-def build_tale_of_tape(bet_name, bet_m, opp_name, opp_m, total_teams) -> List[str]:
+def build_tale_of_tape(bet_name, bet_m, opp_name, opp_m, total_teams, qb_crosswalk=None) -> List[str]:
@@
-        name = clean_qb(mm.get("qbname"))
+        name = clean_qb(mm.get("qbname"), qb_crosswalk)
@@
 def build_article(
     game, game_rows, metrics, records, team_names, schedule_row,
     stat_context, provenance, injury_reports, edge_game_count, model_ranks_df=None,
+    qb_crosswalk=None,
 ) -> Tuple[str, Dict[str, object]]:
@@
-    sections.extend(["| Team name | Best Spread/Odds | Best Book | Model Cover% | Edge | BTB Advice |", "|---|---|---|---|---|---|"])
-    sections.extend([f"| {team} | {spread_odds} | {book} | {cover} | {edge} | {call} |" for team, spread_odds, book, cover, edge, call in matchup_rows])
+    sections.extend(["| Team name | Best Spread/Odds | Best Book | Model Cover% | BTB Advice |", "|---|---|---|---|---|"])
+    sections.extend([f"| {team} | {spread_odds} | {book} | {cover} | {call} |" for team, spread_odds, book, cover, _edge, call in matchup_rows])
@@
-    starters_note = assumed_starters(bet_name, bet_m, opp_name, opp_m)
+    starters_note = assumed_starters(bet_name, bet_m, opp_name, opp_m, qb_crosswalk=qb_crosswalk)
@@
-    tape = build_tale_of_tape(bet_name, bet_m, opp_name, opp_m, total_teams)
+    tape = build_tale_of_tape(bet_name, bet_m, opp_name, opp_m, total_teams, qb_crosswalk=qb_crosswalk)
@@
-    qb_lines = qb_xfactor(bet_name, bet_m, opp_name, opp_m, total_teams, seed)
+    qb_lines = qb_xfactor(bet_name, bet_m, opp_name, opp_m, total_teams, seed, qb_crosswalk=qb_crosswalk)
@@
 def main() -> None:
@@
     spreads, week, season, local_root = load_spreads_and_target_context(args, session)
     model = load_model_data(week, args, local_root, session)
     model_rank_lookup = model_ranks(model)
+    qb_crosswalk = load_qb_crosswalk()
@@
         article, article_payload = build_article(
             game,
             game_rows.copy(),
             metrics.copy(),
             records,
@@
             games_with_edges,
             model_rank_lookup,
+            qb_crosswalk=qb_crosswalk,
         )
