"""Brand-fit score (0-100) used to rank qualified creators.

Each component is normalised to 0-1 and multiplied by its configured weight, and the
per-component points are kept so any ranking can be explained.
"""

from dataclasses import dataclass

from outreach.config import FilterSettings, ScoringSettings

# A creator at the very edge of the allowed size range still gets half the size-fit points;
# the range itself is the hard filter, the sweet spot only adjusts ranking.
EDGE_OF_RANGE_SIZE_FIT = 0.5


@dataclass(frozen=True)
class ScoreInputs:
    relevance: float
    engagement_rate: float
    subscriber_count: int
    uploads_in_activity_window: int
    has_email: bool


@dataclass(frozen=True)
class ScoreBreakdown:
    relevance: float
    engagement: float
    size_fit: float
    activity: float
    contactability: float

    @property
    def total(self) -> float:
        points = self.relevance + self.engagement + self.size_fit + self.activity
        return round(points + self.contactability, 1)

    def as_dict(self) -> dict[str, float]:
        return {
            "relevance": round(self.relevance, 1),
            "engagement": round(self.engagement, 1),
            "size_fit": round(self.size_fit, 1),
            "activity": round(self.activity, 1),
            "contactability": round(self.contactability, 1),
        }


def size_fit(subscribers: int, filters: FilterSettings, scoring: ScoringSettings) -> float:
    """1.0 inside the sweet spot, sloping linearly down to 0.5 at the range edges."""
    if scoring.size_sweet_spot_min <= subscribers <= scoring.size_sweet_spot_max:
        return 1.0
    if subscribers < scoring.size_sweet_spot_min:
        span = scoring.size_sweet_spot_min - filters.subscribers_min
        distance_to_spot = scoring.size_sweet_spot_min - subscribers
    else:
        span = filters.subscribers_max - scoring.size_sweet_spot_max
        distance_to_spot = subscribers - scoring.size_sweet_spot_max
    shortfall = min(distance_to_spot / span, 1.0)
    return 1.0 - (1.0 - EDGE_OF_RANGE_SIZE_FIT) * shortfall


def brand_fit_score(
    inputs: ScoreInputs, scoring: ScoringSettings, filters: FilterSettings
) -> ScoreBreakdown:
    weights = scoring.weights
    engagement = min(inputs.engagement_rate / scoring.engagement_rate_for_full_marks, 1.0)
    activity = min(inputs.uploads_in_activity_window / scoring.uploads_for_full_activity, 1.0)
    return ScoreBreakdown(
        relevance=weights.relevance * inputs.relevance,
        engagement=weights.engagement * engagement,
        size_fit=weights.size_fit * size_fit(inputs.subscriber_count, filters, scoring),
        activity=weights.activity * activity,
        contactability=weights.contactability if inputs.has_email else 0.0,
    )
