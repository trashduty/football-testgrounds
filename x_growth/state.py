from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


class AlertState:

    def __init__(
        self,
        state_path: str,
        keep_days: int = 7,
    ):

        self.path = Path(state_path)
        self.keep_days = keep_days

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.alerted = self._load()
        self.prune()


    def _load(self) -> dict[str, str]:

        if not self.path.exists():
            return {}

        try:

            with self.path.open(
                "r",
                encoding="utf-8",
            ) as f:

                data = json.load(f)

            if isinstance(data, dict):
                return data

        except Exception:
            pass

        return {}


    def has_alerted(self, post_id: str) -> bool:

        return str(post_id) in self.alerted


    def mark_alerted(self, post_id: str):

        self.alerted[str(post_id)] = (
            datetime.now(timezone.utc)
            .isoformat()
        )


    def prune(self):

        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(days=self.keep_days)
        )

        clean = {}

        for post_id, timestamp in self.alerted.items():

            try:

                dt = datetime.fromisoformat(
                    timestamp.replace(
                        "Z",
                        "+00:00",
                    )
                )

                if dt >= cutoff:
                    clean[post_id] = timestamp

            except Exception:
                continue

        self.alerted = clean


    def save(self):

        temp_path = self.path.with_suffix(
            ".tmp"
        )

        with temp_path.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                self.alerted,
                f,
                indent=2,
                sort_keys=True,
            )

        os.replace(
            temp_path,
            self.path,
        )
