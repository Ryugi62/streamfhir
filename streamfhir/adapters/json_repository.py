"""Adapter: JSON file repositories for sites and records."""
import json
from typing import Any, Dict, List, Optional

from ..domain.sites import Site


class JsonSiteRepository:
    def __init__(self, path: str):
        self.path = path

    def all(self) -> Dict[str, Site]:
        with open(self.path, encoding="utf-8") as fh:
            data = json.load(fh)
        return {s["site_id"]: Site(s["site_id"], s["name"], s["lat"], s["lon"], s["water_body"], s.get("description", ""),
                                s.get("flow", "flowing"))
                for s in data["sites"]}


class JsonRecordRepository:
    def __init__(self, path: str):
        self.path = path

    def all(self) -> List[Dict[str, Any]]:
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)["records"]

    def dataset(self) -> Dict[str, Any]:
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh).get("dataset", {})

    def demo_as_of(self) -> Optional[str]:
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh).get("demo_as_of")
