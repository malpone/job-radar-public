"""Title pre-filter: drops titles that are clearly off-track before any text is read.

Rules: out-of-track titles go (pure software developer, street sales, store staff, healthcare or
trade roles, executives, recruiters). For sources that return everything (RSS feeds, company career
pages) a positive signal is also required: the title must touch at least one of the target lanes.
When in doubt the title stays. The lanes below match config/profile.example.yaml; edit them to taste.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RELEVANT = re.compile(
    r"revenue op|rev ?ops|revenue analy|revenue systems|sales op|sales ops|operazioni commerciali|"
    r"\bgtm\b|go[- ]to[- ]market|\bcrm\b|salesforce|hubspot|dynamics 365|"
    r"sales analy|commercial analy|business analy|analista|analyst|"
    r"business operations|bizops|marketing op|marketing automation|automation specialist|ai automation|"
    r"sales enablement|deal desk|sales intelligence|growth op|lead gen|demand gen|"
    r"business intelligence|\bbi\b|power bi|reporting|data analy|analytics|"
    r"pricing|forecast|business controller|sales controller|controller commerciale|controller|controlling|"
    r"customer success op|cs ops|partner op|channel op|commercial excellence|sales excellence",
    re.I,
)

# Technical roles tied to the lanes: "developer/engineer" is not pure software here
TECH_OK = re.compile(
    r"crm|salesforce|hubspot|gtm|go[- ]to[- ]market|revops|revenue|automation|\bbi\b|business intelligence|analytics|data analy",
    re.I,
)

PURE_DEV = re.compile(
    r"\b(software|backend|back-end|frontend|front-end|full[- ]?stack|mobile|ios|android|embedded|firmware|"
    r"devops|sre|site reliability|cloud|platform|security|network|qa|test|game|java|python|\.net|react|node)\b.*\b(engineer|developer|sviluppatore|architect)\b"
    r"|\b(developer|sviluppatore|programmatore)\b",
    re.I,
)

OUT_OF_TRACK = [
    (re.compile(r"\b(director|direttore|direttrice|vice president|\bvp\b|svp|evp|chief|cfo|cto|ceo|coo|cro|cmo)\b", re.I),
     "director or vice president"),
    (re.compile(r"\b(agente|venditore|venditrice|promoter|field sales|sales representative|rappresentante|door to door)\b", re.I),
     "street sales or agent"),
    (re.compile(
        r"\baddett[oa]\b.*\b(vendit|cassa|banco|reparto|scaffal|allestiment|gastronomia|pulizie|magazzino|gdo|negozio)"
        r"|\b(commess[oa]|cassier[ea]|banconista|scaffalista|sales advisor|retail associate|shop assistant|merchandiser)\b"
        r"|\bstore (associate|manager|assistant|staff)\b", re.I), "store staff"),
    (re.compile(
        r"\b(infermier[ea]|oss|medico|nurse|fisioterapist\w*|farmacist\w*|operai[oa]|elettricista|meccanico|cablatore|saldatore|"
        r"magazziniere|autista|carrellista|manutentore|montatore|laboratorio|cyber|soc analyst|security analyst)\b"
        r"|\btecnic[oa]\b(?! commerciale)", re.I), "healthcare or trade role"),
    (re.compile(r"\b(recruiter|talent acquisition|recruiting)\b", re.I), "recruiting role"),
]

PRIORITY_1 = re.compile(
    r"revenue op|rev ?ops|sales op|crm op|\bgtm\b|go[- ]to[- ]market|deal desk|sales enablement|business operations|bizops|revenue analy",
    re.I,
)
PRIORITY_2 = re.compile(
    r"\bcrm\b|hubspot|salesforce|marketing op|marketing automation|sales analy|commercial analy|business intelligence|\bbi\b|power bi",
    re.I,
)


def title_priority(title: str) -> int:
    """1 = main lane (RevOps, Sales Ops, GTM); 2 = CRM, Marketing Ops, BI; 3 = the rest."""
    t = title or ""
    if PRIORITY_1.search(t):
        return 1
    if PRIORITY_2.search(t):
        return 2
    return 3


@dataclass
class PrefilterResult:
    keep: bool
    reason: str = ""


def prefilter_title(title: str) -> PrefilterResult:
    t = (title or "").strip()
    if not t:
        return PrefilterResult(False, "missing title")
    for rx, reason in OUT_OF_TRACK:
        if rx.search(t):
            return PrefilterResult(False, f"title off-track ({reason})")
    if PURE_DEV.search(t) and not TECH_OK.search(t):
        return PrefilterResult(False, "title off-track (pure software developer)")
    if not RELEVANT.search(t):
        return PrefilterResult(False, "title off-track (no target lane in the title)")
    return PrefilterResult(True)
