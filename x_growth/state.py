from __future__ import annotations

import json
import os

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from pathlib import Path


class RadarState:

    def __init__(
        self,
        state_dir: str,
        keep_days: int = 7,
    ):

        self.state_dir = Path(
            state_dir
        )

        self.state_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.alerted_path = (
            self.state_dir
            / "alerted_ids.json"
        )

        self.search_path = (
            self.state_dir
            / "search_runs.json"
        )

        self.keep_days = keep_days

        self.alerted = self._load_json(
            self.alerted_path
        )

        self.search_runs = self._load_json(
            self.search_path
        )

        self.prune_alerted()


    @staticmethod
    def _load_json(
        path: Path,
    ) -> dict:

        if not path.exists():
            return {}

        try:

            with path.open(
                "r",
                encoding="utf-8",
            ) as f:

                data = json.load(f)

            return (
                data
                if isinstance(
                    data,
                    dict,
                )
                else {}
            )

        except Exception:
            return {}


    @staticmethod
    def _save_json(
        path: Path,
        data: dict,
    ):

        temp = path.with_suffix(
            ".tmp"
        )

        with temp.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                indent=2,
                sort_keys=True,
            )

        os.replace(
            temp,
            path,
        )


    def has_alerted(
        self,
        post_id: str,
    ) -> bool:

        return (
            str(post_id)
            in self.alerted
        )


    def mark_alerted(
        self,
        post_id: str,
    ):

        self.alerted[
            str(post_id)
        ] = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )


    def should_run_query(
        self,
        query_name: str,
        cadence_minutes: int,
    ) -> bool:

        previous = self.search_runs.get(
            query_name
        )

        if not previous:
            return True

        try:

            previous_dt = (
                datetime.fromisoformat(
                    previous.replace(
                        "Z",
                        "+00:00",
                    )
                )
            )

        except Exception:
            return True

        elapsed = (
            datetime.now(
                timezone.utc
            )
            - previous_dt
        )

        return (
            elapsed.total_seconds()
            >= cadence_minutes * 60
        )


    def mark_query_run(
        self,
        query_name: str,
    ):

        self.search_runs[
            query_name
        ] = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )


    def prune_alerted(self):

        cutoff = (
            datetime.now(
                timezone.utc
            )
            - timedelta(
                days=self.keep_days
            )
        )

        clean = {}

        for (
            post_id,
            timestamp,
        ) in self.alerted.items():

            try:

                dt = (
                    datetime.fromisoformat(
                        timestamp.replace(
                            "Z",
                            "+00:00",
                        )
                    )
                )

                if dt >= cutoff:

                    clean[
                        post_id
                    ] = timestamp

            except Exception:
                continue

        self.alerted = clean


    def save(self):

        self._save_json(
            self.alerted_path,
            self.alerted,
        )

        self._save_json(
            self.search_path,
            self.search_runs,
        )
