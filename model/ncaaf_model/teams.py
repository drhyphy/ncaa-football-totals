from __future__ import annotations

import re
import unicodedata


ALIASES = {
    "uconn": "connecticut",
    "uconnhuskies": "connecticut",
    "connecticuthuskies": "connecticut",
    "albany": "ualbany",
    "ualbanygreatdanes": "ualbany",
    "albanygreatdanes": "ualbany",
    "miamiflhurricanes": "miamihurricanes",
    "miamihurricanes": "miamihurricanes",
    "miamioh": "miamiohio",
    "miamiohredhawks": "miamiohio",
    "massachusettsminutemen": "massachusetts",
    "umassminutemen": "massachusetts",
    "umass": "massachusetts",
    "hawaiirainbowwarriors": "hawaii",
    "floridainternationalpanthers": "fiu",
    "floridainternational": "fiu",
    "northcarolinastate": "ncstate",
    "northcarolinastatewolfpack": "ncstate",
    "youngstownstpenguins": "youngstownstatepenguins",
    "appalachianstatemountaineers": "appstatemountaineers",
    "thecitadelbulldogs": "citadelbulldogs",
    "southernmississippigoldeneagles": "southernmissgoldeneagles",
    "houstonbaptisthuskies": "houstonchristianhuskies",
    "samhousonstatebearkats": "samhoustonbearkats",
    "samhoustonstatebearkats": "samhoustonbearkats",
    "southeasternlouisianalions": "selouisianalions",
    "southerncalifornia": "usc",
    "usctrojans": "usc",
    "centralflorida": "ucf",
    "texassanelpaso": "utep",
    "louisianamonroe": "ulmonroe",
    "louisianalafayette": "louisiana",
    # Exact public-feed variants verified against the September 12, 2026
    # ESPN schedule, opponent identity, kickoff time and ESPN game identifier.
    "tennesseemartinskyhawks": "utmartinskyhawks",
    "centralconnecticutstatebluedevils": "centralconnecticutbluedevils",
    "mercyhurstuniversitylakers": "mercyhurstlakers",
    "delawarefightinbluehens": "delawarebluehens",
    "southernuniversityjaguars": "southernjaguars",
    "gramblingstatetigers": "gramblingtigers",
}


def normalize_team(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("&", "and")
    cleaned = re.sub(r"[^a-z0-9]+", "", text)
    return ALIASES.get(cleaned, cleaned)


def pair_key(away: str, home: str) -> str:
    return f"{normalize_team(away)}@{normalize_team(home)}"


def best_prefix_match(team: str, candidates: list[str], minimum: int = 3) -> str | None:
    target = normalize_team(team)
    exact = [candidate for candidate in candidates if normalize_team(candidate) == target]
    if exact:
        return exact[0]
    matches: list[tuple[int, str]] = []
    for candidate in candidates:
        normalized = normalize_team(candidate)
        if len(normalized) >= minimum and (target.startswith(normalized) or normalized.startswith(target)):
            matches.append((min(len(target), len(normalized)), candidate))
    return max(matches, default=(0, None))[1]
