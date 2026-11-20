"""Data model and the abstract scraper shared by all sources."""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from jobradar.common import honest_headers, load_config

logger = logging.getLogger(__name__)


@dataclass
class RawJob:
    """A job ad as it comes from a source, before filtering."""

    source: str
    url_origin: str
    job_title: str
    company_name: str
    external_id: str | None = None
    location_raw: str | None = None
    location_type: str | None = None  # 'on-site' | 'hybrid' | 'remote' | None
    contract_type: str | None = None
    salary_raw: str | None = None
    seniority_level: str | None = None
    description_raw: str | None = None
    tags: list[str] = field(default_factory=list)
    date_posted: date | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScraperResult:
    source: str
    started_at: datetime
    finished_at: datetime
    status: str  # 'success' | 'failed'
    jobs: list[RawJob]
    errors: list[str] = field(default_factory=list)


class BaseScraper(ABC):
    """Base class: retries, polite pauses and a run() wrapper that never raises."""

    SOURCE_NAME: str = ""
    ACCEPT = "application/json"
    DEFAULT_DELAY = 2.0

    def __init__(self, config: dict[str, Any] | None = None, request_timeout_seconds: int = 30):
        if not self.SOURCE_NAME:
            raise ValueError(f"{self.__class__.__name__} must define SOURCE_NAME")
        self.config = config or load_config()
        self.request_delay = self.DEFAULT_DELAY
        self.request_timeout = request_timeout_seconds
        self._last_request_time = 0.0
        self.session = requests.Session()
        self.session.headers.update(honest_headers(self.config.get("user_agent"), accept=self.ACCEPT))

    def source_config(self) -> dict[str, Any]:
        return self.config.get("sources", {}).get(self.SOURCE_NAME, {}) or {}

    @abstractmethod
    def fetch_jobs(self) -> list[RawJob]:
        """Return every ad this source offers. Handle pagination and parsing here."""

    def run(self) -> ScraperResult:
        started = datetime.now(timezone.utc)
        jobs: list[RawJob] = []
        errors: list[str] = []
        status = "failed"
        try:
            jobs = self.fetch_jobs()
            status = "success" if jobs else "failed"
        except Exception as e:  # one source failing must not stop the others
            errors.append(f"{type(e).__name__}: {e}")
            logger.exception("Source failed: %s", self.SOURCE_NAME)
        logger.info("Source %s | status=%s | jobs=%d", self.SOURCE_NAME, status, len(jobs))
        return ScraperResult(self.SOURCE_NAME, started, datetime.now(timezone.utc), status, jobs, errors)

    def _sleep_between_requests(self, min_seconds: float | None = None, max_seconds: float | None = None) -> None:
        low = self.request_delay if min_seconds is None else min_seconds
        delay = random.uniform(low, max_seconds) if max_seconds and max_seconds > low else low
        elapsed = time.time() - self._last_request_time
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request_time = time.time()

    @retry(
        retry=retry_if_exception_type(
            (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.HTTPError)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def _request_with_retry(self, url: str, params: Any = None, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.request_timeout)
        response = self.session.get(url, params=params, **kwargs)
        response.raise_for_status()
        return response

    def close(self) -> None:
        self.session.close()
