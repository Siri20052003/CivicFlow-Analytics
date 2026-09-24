"""District-level service metrics with uncertainty and small-cohort safeguards."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import sqrt

from civicflow.model import ServiceCase
from civicflow.validation import validate_cases

MIN_CLOSED_CASES = 30


@dataclass(frozen=True, slots=True)
class DistrictProfile:
    name: str
    latitude: float
    longitude: float
    population: int


# Fictional district centroids and populations for a synthetic city; not real boundaries.
DISTRICT_PROFILES: dict[int, DistrictProfile] = {
    1: DistrictProfile("North River", 30.345, -97.744, 54_200),
    2: DistrictProfile("Highland", 30.326, -97.710, 48_900),
    3: DistrictProfile("East Junction", 30.302, -97.683, 61_400),
    4: DistrictProfile("Civic Center", 30.274, -97.742, 43_700),
    5: DistrictProfile("Lakeside", 30.260, -97.698, 50_600),
    6: DistrictProfile("South Ridge", 30.226, -97.734, 57_800),
    7: DistrictProfile("West Hills", 30.294, -97.790, 46_300),
    8: DistrictProfile("University", 30.287, -97.724, 39_500),
    9: DistrictProfile("Airport Corridor", 30.238, -97.665, 52_100),
    10: DistrictProfile("Greenbelt", 30.248, -97.786, 45_500),
}


@dataclass(frozen=True, slots=True)
class DistrictServiceMetric:
    district: int
    district_name: str
    latitude: float
    longitude: float
    population: int
    total_cases: int
    closed_cases: int
    open_cases: int
    cases_per_1000_residents: float
    compliance_rate: float | None
    confidence_lower: float | None
    confidence_upper: float | None
    citywide_compliance_rate: float
    gap_percentage_points: float | None
    comparison: str
    suppression_reason: str | None


def wilson_interval(successes: int, total: int, *, z_score: float = 1.96) -> tuple[float, float]:
    """Calculate a Wilson score interval for a binomial proportion."""
    if total < 1:
        raise ValueError("total must be positive")
    if not 0 <= successes <= total:
        raise ValueError("successes must be between zero and total")
    proportion = successes / total
    denominator = 1 + z_score**2 / total
    center = (proportion + z_score**2 / (2 * total)) / denominator
    margin = (
        z_score
        * sqrt(proportion * (1 - proportion) / total + z_score**2 / (4 * total**2))
        / denominator
    )
    return round(center - margin, 4), round(center + margin, 4)


def district_metric_from_counts(
    district: int,
    *,
    total_cases: int,
    closed_cases: int,
    compliant_cases: int,
    citywide_compliance_rate: float,
    min_closed_cases: int = MIN_CLOSED_CASES,
) -> DistrictServiceMetric:
    """Create one district metric while enforcing publication thresholds."""
    profile = DISTRICT_PROFILES[district]
    if min_closed_cases < 1:
        raise ValueError("min_closed_cases must be positive")
    if not 0 <= compliant_cases <= closed_cases <= total_cases:
        raise ValueError("district counts are inconsistent")

    base = {
        "district": district,
        "district_name": profile.name,
        "latitude": profile.latitude,
        "longitude": profile.longitude,
        "population": profile.population,
        "total_cases": total_cases,
        "closed_cases": closed_cases,
        "open_cases": total_cases - closed_cases,
        "cases_per_1000_residents": round(total_cases / profile.population * 1000, 2),
        "citywide_compliance_rate": round(citywide_compliance_rate, 4),
    }
    if closed_cases < min_closed_cases:
        return DistrictServiceMetric(
            **base,
            compliance_rate=None,
            confidence_lower=None,
            confidence_upper=None,
            gap_percentage_points=None,
            comparison="suppressed",
            suppression_reason=f"fewer than {min_closed_cases} closed cases",
        )

    rate = compliant_cases / closed_cases
    lower, upper = wilson_interval(compliant_cases, closed_cases)
    if upper < citywide_compliance_rate:
        comparison = "statistically_lower"
    elif lower > citywide_compliance_rate:
        comparison = "statistically_higher"
    else:
        comparison = "no_clear_difference"
    return DistrictServiceMetric(
        **base,
        compliance_rate=round(rate, 4),
        confidence_lower=lower,
        confidence_upper=upper,
        gap_percentage_points=round((rate - citywide_compliance_rate) * 100, 2),
        comparison=comparison,
        suppression_reason=None,
    )


def build_district_service_report(
    cases: list[ServiceCase], *, min_closed_cases: int = MIN_CLOSED_CASES
) -> list[DistrictServiceMetric]:
    """Compare geographic service outcomes without using demographic attributes."""
    validate_cases(cases)
    closed = [case for case in cases if case.met_sla is not None]
    if not closed:
        raise ValueError("district reporting requires at least one closed case")
    citywide_rate = sum(case.met_sla is True for case in closed) / len(closed)
    grouped: dict[int, list[ServiceCase]] = defaultdict(list)
    for case in cases:
        grouped[case.district].append(case)

    return [
        district_metric_from_counts(
            district,
            total_cases=len(rows),
            closed_cases=sum(case.met_sla is not None for case in rows),
            compliant_cases=sum(case.met_sla is True for case in rows),
            citywide_compliance_rate=citywide_rate,
            min_closed_cases=min_closed_cases,
        )
        for district, rows in sorted(grouped.items())
    ]
