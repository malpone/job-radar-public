"""Geographic filter.

- Remote: accepted if open to Europe, EMEA, CET or worldwide. Dropped if reserved to countries outside
  Europe. "UK only" is kept with a note (right to work needed): the scoring rubric caps it.
- On-site: within `on_site_max_km` great-circle km from home.
- Hybrid: within `hybrid_max_km`, plus the extra areas in the config.
- On-site or hybrid outside the accepted countries: dropped.

Distance is computed on the city, never on a list of countries. Cities come from the config
gazetteer; an optional geocoder object (anything with `.geocode(query)` returning an object with
`latitude`, `longitude`, `country_iso2`) can resolve cities that are missing, for local countries only.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from jobradar.etl.text import haversine_km
from jobradar.models import RawJob

logger = logging.getLogger(__name__)

EU_ISO2 = {
    "it", "sm", "va", "de", "fr", "es", "pt", "nl", "be", "lu", "at", "ie", "pl", "cz", "sk", "hu", "ro", "bg",
    "gr", "hr", "si", "se", "dk", "fi", "ee", "lv", "lt", "mt", "cy", "ch", "no", "is", "li",
}
EU_REGIONS = {"eu", "europe", "emea", "global", "worldwide", "anywhere", "dach", "nordics", "benelux", "cet"}
NON_EU_REGIONS = {"north_america", "latam", "south_america", "apac", "asia", "oceania", "africa", "middle_east", "us", "usa"}

EU_TEXT = re.compile(
    r"europe|european|\beu\b|emea|worldwide|world|anywhere|global|\bcet\b|\bcest\b|gmt\s*\+\s*[0-3]\b|utc\s*\+\s*[0-3]\b|"
    r"ital|germany|deutschland|france|spain|españa|portugal|netherlands|nederland|belgium|austria|ireland|poland|"
    r"czech|hungary|romania|bulgaria|greece|croatia|slovenia|slovakia|sweden|denmark|finland|estonia|latvia|"
    r"lithuania|luxembourg|malta|cyprus|switzerland|norway|iceland|remote\s*-\s*eu",
    re.I,
)
UK_TEXT = re.compile(r"\buk\b|united kingdom|england|scotland|wales|london|great britain", re.I)
NON_EU_TEXT = re.compile(
    r"\bus\b|\busa\b|united states|u\.s\.|canada|north america|americas|latam|latin america|mexico|brazil|argentina|"
    r"apac|asia|india|philippines|pakistan|australia|new zealand|singapore|japan|china|africa|nigeria|kenya|"
    r"middle east|israel|uae|dubai|saudi|turkey",
    re.I,
)
US_ONLY_DESC = re.compile(
    r"(must|need to|required to)\s+(be\s+)?(based|located|reside|living)\s+in\s+(the\s+)?(us|u\.s\.|usa|united states|canada|north america)"
    r"|\b(us|usa|u\.s\.)[- ]only\b|only (open to|accepting) (candidates|applicants) (in|from) (the )?(us|usa|united states)"
    r"|authori[sz]ed to work in the (us|united states)",
    re.I,
)
HYBRID_TEXT = re.compile(r"hybrid|ibrid", re.I)
REMOTE_TEXT = re.compile(r"\bremote\b|remoto|da remoto|full[- ]remote|smart working|home[- ]based", re.I)
ITALY_TEXT = re.compile(r"ital|,\s*it\b|\(it\)|san marino", re.I)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s.lower()).strip(" -,.")


@dataclass
class GeoDecision:
    keep: bool
    reason: str
    mode: str
    km: float | None = None
    place: str | None = None
    notes: list[str] = field(default_factory=list)


class GeoFilter:
    def __init__(self, config: dict[str, Any], geocoder: Any = None):
        home = config.get("home", {})
        geo = config.get("geo", {})
        self.home = (float(home["lat"]), float(home["lon"]))
        self.on_site_km = float(geo.get("on_site_max_km", 50))
        self.hybrid_km = float(geo.get("hybrid_max_km", 100))
        self.extra_areas = geo.get("hybrid_extra_areas", []) or []
        self.local_countries = {c.lower() for c in geo.get("local_countries", ["it"])}
        self.gazetteer = {_norm(k): (float(v[0]), float(v[1])) for k, v in (config.get("gazetteer") or {}).items()}
        self.geocoder = geocoder

    def infer_mode(self, job: RawJob) -> str:
        if job.location_type in ("remote", "hybrid", "on-site"):
            return job.location_type
        text = f"{job.location_raw or ''} {job.job_title or ''}"
        if HYBRID_TEXT.search(text):
            return "hybrid"
        if REMOTE_TEXT.search(text):
            return "remote"
        return "on-site"

    def decide(self, job: RawJob) -> GeoDecision:
        mode = self.infer_mode(job)
        return self._decide_remote(job) if mode == "remote" else self._decide_local(job, mode)

    # ------------------------------------------------------------ remote
    def _decide_remote(self, job: RawJob) -> GeoDecision:
        md = job.metadata or {}
        regions = {r.lower() for r in md.get("regions", [])}
        countries = {c.lower() for c in md.get("countries", [])}
        loc = " ".join([job.location_raw or "", " ".join(md.get("countries_named", [])),
                        md.get("region", "") or "", md.get("country", "") or ""])

        if md.get("geo_filter_api") == "europe":
            return GeoDecision(True, "remote, Europe (source filter)", "remote", place=job.location_raw)
        if regions & EU_REGIONS or countries & EU_ISO2:
            return GeoDecision(True, "remote, open to Europe", "remote", place=job.location_raw)
        if EU_TEXT.search(loc):
            return GeoDecision(True, "remote, open to Europe", "remote", place=job.location_raw)
        if UK_TEXT.search(loc) or "uk" in regions or "gb" in countries:
            return GeoDecision(True, "remote, UK only", "remote", place=job.location_raw,
                               notes=["UK only: right to work in the UK is needed"])
        if (regions and regions <= NON_EU_REGIONS) or (countries and not countries & EU_ISO2) or NON_EU_TEXT.search(loc):
            return GeoDecision(False, "remote reserved to countries outside Europe", "remote", place=job.location_raw)
        if US_ONLY_DESC.search((job.description_raw or "")[:3000]):
            return GeoDecision(False, "remote reserved to the United States (from the text)", "remote")
        return GeoDecision(True, "remote, area not indicated", "remote", place=job.location_raw,
                           notes=["remote area not indicated: verify"])

    # ------------------------------------------------------------ on-site / hybrid
    def _candidates(self, job: RawJob) -> list[str]:
        md = job.metadata or {}
        out = [str(c) for c in (md.get("cities", []) or [])]
        for part in re.split(r"[;,/|]| - ", job.location_raw or ""):
            part = part.strip()
            if part:
                out.append(part)
        seen, uniq = set(), []
        for c in out:
            n = _norm(c)
            if n and n not in seen:
                seen.add(n)
                uniq.append(c)
        return uniq

    def _country_known_outside(self, job: RawJob) -> bool:
        countries = {c.lower() for c in (job.metadata or {}).get("countries", [])}
        if countries:
            return not (countries & self.local_countries)
        loc = job.location_raw or ""
        if "it" in self.local_countries and ITALY_TEXT.search(loc):
            return False
        return bool(EU_TEXT.search(loc) or NON_EU_TEXT.search(loc) or UK_TEXT.search(loc))

    def _locate(self, name: str) -> tuple[float, float] | None:
        n = _norm(name)
        if n in self.gazetteer:
            return self.gazetteer[n]
        first = _norm(re.split(r"[(\d]", name)[0])  # "Milan (MI)" -> "milan"
        for key in (first, first.split(" ")[0] if first else ""):
            if key and key in self.gazetteer:
                return self.gazetteer[key]
        if self.geocoder and len(n) >= 3 and not re.fullmatch(r"[a-z]{2}", n):
            try:
                res = self.geocoder.geocode(name)
            except Exception as e:  # a geocoder failure must not stop the pipeline
                logger.debug("geocoding failed for %s: %s", name, e)
                res = None
            if res and str(getattr(res, "country_iso2", "") or "").lower() in self.local_countries:
                return (float(res.latitude), float(res.longitude))
        return None

    def _decide_local(self, job: RawJob, mode: str) -> GeoDecision:
        label = "on-site" if mode == "on-site" else "hybrid"
        if self._country_known_outside(job):
            return GeoDecision(False, f"{label} outside the accepted countries", mode, place=job.location_raw)

        best: tuple[float, str, tuple[float, float]] | None = None
        for cand in self._candidates(job):
            coords = self._locate(cand)
            if coords:
                km = haversine_km(self.home[0], self.home[1], coords[0], coords[1])
                if best is None or km < best[0]:
                    best = (km, cand, coords)
        if best is None:
            return GeoDecision(False, f"{label}, location cannot be determined", mode, place=job.location_raw)

        km, place, coords = best
        km_r = round(km)
        if mode == "on-site":
            ok = km <= self.on_site_km
            return GeoDecision(ok, f"on-site at {km_r} km" + ("" if ok else f" (over {int(self.on_site_km)})"), mode, km_r, place)
        if km <= self.hybrid_km:
            return GeoDecision(True, f"hybrid at {km_r} km", mode, km_r, place)
        for area in self.extra_areas:
            d = haversine_km(coords[0], coords[1], float(area["lat"]), float(area["lon"]))
            if d <= float(area.get("radius_km", 20)):
                return GeoDecision(True, f"hybrid at {km_r} km ({area['name']} area, exception)", mode, km_r, place)
        return GeoDecision(False, f"hybrid at {km_r} km (over {int(self.hybrid_km)})", mode, km_r, place)
