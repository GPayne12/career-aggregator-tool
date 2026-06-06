from typing import List

SYNONYMS = {
    "lxd": ["learning experience designer", "learning experience design"],
    "l&d": ["learning and development", "learning & development"],
    "elearning": ["e-learning", "online learning"],
    "e-learning": ["elearning", "online learning"],
    "isd": ["instructional systems designer", "instructional systems design"],
    "instructional design": ["instructional designer"],
    "learning design": ["learning designer"],
    "learning and development": ["l&d", "learning & development"],
    "learning experience design": ["lxd", "learning experience designer"],
    "curriculum design": ["curriculum designer", "curriculum developer"],
    "course design": ["course designer", "course developer"],
}


def expand_keywords(keywords: List[str]) -> List[str]:
    """Return keywords plus all synonym expansions, deduplicated by lowercase."""
    result = []
    seen: set = set()
    for kw in keywords:
        for term in [kw] + SYNONYMS.get(kw.lower(), []):
            if term.lower() not in seen:
                result.append(term)
                seen.add(term.lower())
    return result
