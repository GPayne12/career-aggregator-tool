"""Where a posting is, read from its free-text location, for Survey's Where filter.

  "na"        continental North America: the US (not Hawaii) and Canada
  "corridor"  where George would relocate: DC/Virginia up to Boston, plus remote
              roles in North America (remote needs no move)
  "any"       no filter

Locations come in many shapes ("US-CA-Menlo Park", "USA.VA.Reston", "FR-Paris",
"London, UK; Ontario, CAN; Remote-Friendly, United States", "2 Locations").
A posting with several places counts if any of them qualifies. One whose place
can't be read ("2 Locations", "N/A", "Remote") is kept: it may well qualify.
"""
import re
from typing import Optional

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
US_ABBR = set(US_STATES.values())
CA_PROVINCES = {"ontario", "british columbia", "quebec", "québec", "alberta", "manitoba", "saskatchewan", "nova scotia",
                "new brunswick", "newfoundland", "prince edward island"}
CA_ABBR = {"ON", "BC", "QC", "AB", "MB", "SK", "NS", "NB", "NL", "PE"}
NA_CITIES = {"new york city", "nyc", "san francisco", "bay area", "san jose", "silicon valley", "seattle", "boston", "chicago",
             "austin", "denver", "atlanta", "los angeles", "san diego", "portland", "washington dc", "washington, dc", "washington d.c.",
             "toronto", "vancouver", "montreal", "montréal", "calgary", "ottawa", "waterloo", "philadelphia", "pittsburgh", "miami",
             "dallas", "houston", "phoenix", "salt lake city", "minneapolis", "detroit", "nashville", "raleigh", "durham",
             "brooklyn", "manhattan", "menlo park", "palo alto", "mountain view", "sunnyvale", "redwood city", "san mateo",
             "cambridge", "arlington", "reston", "mclean", "bethesda", "baltimore", "newark", "jersey city", "princeton",
             "stamford", "hartford", "new haven", "providence", "somerville", "waltham"}
STRONG_NA = re.compile(r"\b(united states|u\.s\.a?\.?|usa|us|canada|can|north america|americas)\b", re.I)
OFF_NAMES = {"ireland", "dublin", "london", "united kingdom", "england", "scotland", "wales", "paris", "france", "germany",
             "berlin", "munich", "hamburg", "frankfurt", "tokyo", "japan", "singapore", "india", "bengaluru", "bangalore", "hyderabad",
             "pune", "mumbai", "delhi", "chennai", "brazil", "campinas", "são paulo", "sao paulo", "mexico", "costa rica", "bulgaria",
             "sofia", "hungary", "budapest", "portugal", "lisbon", "spain", "madrid", "barcelona", "netherlands", "amsterdam",
             "australia", "melbourne", "sydney", "new zealand", "auckland", "belgium", "brussels", "poland", "warsaw", "krakow",
             "israel", "tel aviv", "korea", "seoul", "china", "shanghai", "beijing", "shenzhen", "hong kong", "taiwan", "taipei",
             "philippines", "manila", "vietnam", "argentina", "buenos aires", "colombia", "bogota", "chile", "santiago", "romania",
             "bucharest", "czech", "prague", "switzerland", "zurich", "geneva", "sweden", "stockholm", "denmark", "copenhagen",
             "norway", "oslo", "finland", "helsinki", "austria", "vienna", "italy", "milan", "rome", "greece", "athens", "turkey",
             "istanbul", "dubai", "uae", "united arab emirates", "saudi", "riyadh", "south africa", "cape town", "johannesburg",
             "kenya", "nairobi", "nigeria", "lagos", "egypt", "cairo", "emea", "apac", "latam", "europe", "asia", "estonia",
             "tallinn", "latvia", "lithuania", "serbia", "belgrade", "croatia", "ukraine", "kyiv", "malaysia", "kuala lumpur",
             "thailand", "bangkok", "indonesia", "jakarta", "pakistan", "uk", "luxembourg", "edinburgh", "manchester", "cork",
             "armenia", "yerevan", "georgia tbilisi", "tbilisi", "kazakhstan", "almaty"}
OFF_CODES = {"UK", "GB", "GBR", "FR", "FRA", "DE", "DEU", "ES", "ESP", "IT", "ITA", "IE", "IRL", "NL", "NLD", "AU", "AUS", "NZ",
             "IN", "IND", "SG", "SGP", "JP", "JPN", "BR", "BRA", "MX", "MEX", "BE", "BEL", "CH", "CHE", "SE", "SWE", "DK", "NO",
             "FI", "PL", "POL", "PT", "PRT", "IL", "ISR", "KR", "KOR", "CN", "CHN", "HK", "PH", "PHL", "AE", "ZA", "CR", "AR",
             "CO", "CL", "BG", "HU", "RO", "CZ", "AT", "GR", "TR", "EMEA", "APAC", "LATAM", "EU"}
HAWAII = re.compile(r"\b(hawaii|honolulu)\b|,\s*HI\b", re.I)
CORRIDOR_ABBR = {"DC", "VA", "MD", "DE", "PA", "NJ", "NY", "CT", "RI", "MA"}
CORRIDOR_NAMES = {"district of columbia", "washington dc", "washington, dc", "washington d.c.", "virginia", "maryland",
                  "delaware", "pennsylvania", "new jersey", "new york", "nyc", "connecticut", "rhode island", "massachusetts",
                  "arlington", "alexandria", "reston", "mclean", "herndon", "fairfax", "tysons", "baltimore", "bethesda",
                  "rockville", "philadelphia", "pittsburgh", "newark", "jersey city", "hoboken", "princeton", "brooklyn",
                  "manhattan", "stamford", "hartford", "new haven", "providence", "boston", "cambridge", "somerville", "waltham"}
NOT_WEST_VA = re.compile(r"\bwest virginia\b", re.I)


def _has(text: str, names) -> bool:
    low = text.lower()
    return any(re.search(r"(?<![a-z])" + re.escape(n) + r"(?![a-z])", low) for n in names)


def _codes(part: str) -> set:
    """Two-letter state or province codes in the shapes postings use: "City, ST", "US-ST-City", "USA.ST.City", "ST, City"."""
    found = set()
    for pat in (r",\s*([A-Z]{2})\b", r"\bUSA?[-.\s]+([A-Z]{2})\b", r"^([A-Z]{2})[-,.]\s*[A-Z][a-z]"):
        found.update(re.findall(pat, part))
    return found


def _lead_code(part: str) -> Optional[str]:
    m = re.match(r"\s*([A-Z]{2,4})(?:[-.,\s]|$)", part)
    return m.group(1) if m else None


def _parts(loc: str):
    return [p.strip() for p in re.split(r"[;|•/\n]|\bor\b", loc or "") if p.strip()]


def part_kind(part: str) -> str:
    """'na', 'off' (outside continental North America) or 'unknown'."""
    if HAWAII.search(part):
        return "off"
    strong = bool(STRONG_NA.search(part))
    lead = _lead_code(part)
    # A three-letter country code anywhere ("Remote - ESP CT", "GBR, Remote") places it too.
    three = {t for t in re.findall(r"\b[A-Z]{3,5}\b", part) if t in OFF_CODES}
    if not strong and (_has(part, OFF_NAMES) or three or (lead in OFF_CODES and lead not in US_ABBR | CA_ABBR)):
        return "off"
    codes = _codes(part)
    if strong or codes & (US_ABBR | CA_ABBR) or _has(part, US_STATES.keys()) or _has(part, CA_PROVINCES) or _has(part, NA_CITIES):
        return "na"
    if lead in OFF_CODES:
        return "off"
    return "unknown"


def in_north_america(loc: Optional[str]) -> bool:
    kinds = [part_kind(p) for p in _parts(loc or "")]
    return not kinds or "na" in kinds or all(k == "unknown" for k in kinds)


def in_corridor(loc: Optional[str], location_type: Optional[str] = None) -> bool:
    """DC/Virginia up to Boston, or remote in North America (a remote role needs no move)."""
    if not in_north_america(loc):
        return False
    text = loc or ""
    if location_type == "remote" or re.search(r"\bremote\b", text, re.I):
        return True
    for p in _parts(text):
        if part_kind(p) != "na":
            continue
        if _codes(p) & CORRIDOR_ABBR or (_has(p, CORRIDOR_NAMES) and not NOT_WEST_VA.search(p)):
            return True
    return False


def keep(loc: Optional[str], region: Optional[str], location_type: Optional[str] = None) -> bool:
    if region == "na":
        return in_north_america(loc)
    if region == "corridor":
        return in_corridor(loc, location_type)
    return True
