"""Detects templated output: two emails sharing most of their word trigrams were not
genuinely written for different creators."""

from dataclasses import dataclass
from itertools import combinations

from outreach.personalize.validators import WORD_PATTERN

NGRAM_SIZE = 3


@dataclass(frozen=True)
class NearDuplicate:
    other_id: int
    similarity: float


def word_trigrams(text: str) -> set[tuple[str, ...]]:
    words = WORD_PATTERN.findall(text.lower())
    return {tuple(words[i : i + NGRAM_SIZE]) for i in range(len(words) - NGRAM_SIZE + 1)}


def jaccard_similarity(first: set, second: set) -> float:
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def find_near_duplicates(texts_by_id: dict[int, str], threshold: float) -> dict[int, NearDuplicate]:
    """For each text above the threshold, its single most similar counterpart."""
    trigrams = {item_id: word_trigrams(text) for item_id, text in texts_by_id.items()}
    closest: dict[int, NearDuplicate] = {}
    for first_id, second_id in combinations(trigrams, 2):
        similarity = jaccard_similarity(trigrams[first_id], trigrams[second_id])
        if similarity < threshold:
            continue
        for item_id, other_id in ((first_id, second_id), (second_id, first_id)):
            if item_id not in closest or similarity > closest[item_id].similarity:
                closest[item_id] = NearDuplicate(other_id, similarity)
    return closest
