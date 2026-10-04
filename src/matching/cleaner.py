import re

def generate_search_variations(query: str) -> list[str]:
    """
    Generates variations of a search query, ordered from most specific to least specific.
    This guarantees that if an exact match fails, we progressively try fuzzier matches to maximize hits.
    """
    variations = []
    
    # 1. Original query
    original = query.strip()
    if original:
        variations.append(original)
        
    cleaned = original.lower()

    # Common YouTube noise patterns
    noise_patterns = [
        r'\((official|lyric|music|audio|video|visualizer|live|acoustic|cover).*?\)',
        r'\[(official|lyric|music|audio|video|visualizer|live|acoustic|cover).*?\]',
        r'(official|lyric|music|audio|video|visualizer|live|acoustic|cover)',
        r'[\(\[\{].*?(official|audio|video|hd|hq).*?[\)\]\}]',
    ]

    for pattern in noise_patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    if cleaned and cleaned not in variations:
        variations.append(cleaned)

    # 2. Aggressively strip features (often confuses strict search engines like Tidal's)
    # e.g., "Song Name feat. Artist B" -> "Song Name"
    no_feat = re.sub(r'\b(ft\.?|feat\.?|featuring)\b.*', '', cleaned, flags=re.IGNORECASE).strip()
    if no_feat and no_feat not in variations:
        variations.append(no_feat)
        
    # 3. Strip all punctuation (just alphanumeric and spaces)
    no_punct = re.sub(r'[^\w\s]', ' ', no_feat)
    no_punct = re.sub(r'\s+', ' ', no_punct).strip()
    if no_punct and no_punct not in variations:
        variations.append(no_punct)
        
    # 4. If it's still failing, just try the first few words (highly fuzzy)
    words = no_punct.split()
    if len(words) > 3:
        short_query = " ".join(words[:3])
        if short_query not in variations:
            variations.append(short_query)

    return variations
