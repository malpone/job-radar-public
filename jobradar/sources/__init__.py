"""Source scrapers. Each one returns RawJob objects; failures never stop the other sources."""

from jobradar.sources.eudatajobs import EUDataJobsScraper
from jobradar.sources.freehire import FreehireScraper
from jobradar.sources.himalayas import HimalayasScraper
from jobradar.sources.jobicy import JobicyScraper
from jobradar.sources.randstad import RandstadScraper
from jobradar.sources.weworkremotely import WeWorkRemotelyScraper
from jobradar.sources.workingnomads import WorkingNomadsScraper

REGISTRY = {
    "weworkremotely": WeWorkRemotelyScraper,
    "himalayas": HimalayasScraper,
    "jobicy": JobicyScraper,
    "workingnomads": WorkingNomadsScraper,
    "freehire": FreehireScraper,
    "randstad": RandstadScraper,
    "eudatajobs": EUDataJobsScraper,
}

LABELS = {
    "weworkremotely": "We Work Remotely",
    "himalayas": "Himalayas",
    "jobicy": "Jobicy",
    "workingnomads": "Working Nomads",
    "freehire": "freehire",
    "randstad": "Randstad Italia",
    "eudatajobs": "EU Data Jobs",
    "email": "Email alert",
}
