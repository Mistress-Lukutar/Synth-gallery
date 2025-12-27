"""Tag dictionary for photo tagging suggestions"""

import random

TAG_DICTIONARY = [
    # Nature
    "nature", "landscape", "mountain", "forest", "tree", "flower", "sky", "cloud",
    "sunset", "sunrise", "ocean", "beach", "lake", "river", "waterfall", "garden",

    # Animals
    "animal", "dog", "cat", "bird", "horse", "wildlife", "pet", "fish", "insect",

    # People
    "portrait", "people", "family", "friends", "group", "child", "baby", "couple",

    # Urban
    "city", "street", "building", "architecture", "bridge", "road", "car", "night",

    # Activities
    "travel", "vacation", "sport", "party", "celebration", "wedding", "birthday",
    "concert", "festival", "hiking", "camping", "swimming",

    # Food
    "food", "meal", "restaurant", "cooking", "dessert", "drink", "coffee",

    # Objects
    "art", "book", "music", "vintage", "technology", "fashion", "jewelry",

    # Mood/Style
    "beautiful", "colorful", "black and white", "minimalist", "abstract",
    "macro", "panorama", "reflection", "silhouette",

    # Seasons/Weather
    "spring", "summer", "autumn", "winter", "rain", "snow", "fog",

    # Time
    "morning", "evening", "golden hour", "blue hour",
]


def get_all_tags() -> list[str]:
    """Returns all tags from dictionary"""
    return sorted(TAG_DICTIONARY)


def search_tags(query: str) -> list[str]:
    """Search tags that start with or contain the query"""
    query = query.lower().strip()
    if not query:
        return []

    # Prioritize tags that start with query, then contain query
    starts_with = [t for t in TAG_DICTIONARY if t.startswith(query)]
    contains = [t for t in TAG_DICTIONARY if query in t and t not in starts_with]

    return sorted(starts_with) + sorted(contains)


def is_known_tag(tag: str) -> bool:
    """Check if tag exists in dictionary"""
    return tag.lower().strip() in TAG_DICTIONARY


def get_random_tags(count: int = 3) -> list[str]:
    """Returns random tags from dictionary"""
    count = min(count, len(TAG_DICTIONARY))
    return random.sample(TAG_DICTIONARY, count)
