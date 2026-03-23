from __future__ import annotations

from enum import Enum


class MatchMode(str, Enum):
    title_and_url = "title_and_url"
    title_only = "title_only"
    url_only = "url_only"
