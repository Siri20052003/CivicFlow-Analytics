"""Leakage-safe, calibrated SLA-breach prediction and drift monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from civicflow.model import ServiceCase
from civicflow.validation import validate_cases

CATEGORICAL_FEATURES = ["department", "service_type", "priority", "channel", "district"]
NUMERIC_FEATURES = ["opened_hour", "opened_weekday"]
MODEL_FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES
LEAKAGE_FIELDS = {
    "closed_at",
    "current_status",
    "satisfaction_score",
    "status",
    "target_hours",
    "resolution_hours",
    "sla_breached",
}


@dataclass(frozen=True, slots=True)
class FeatureEffect:
    feature: str
    coefficient: float
    direction: str


@dataclass(frozen=True, slots=True)
class PredictionReport:
    closed_cases: int
    training_cases: int
    calibration_cases: int
    test_cases: int
    training_period_start: str
    training_period_end: str
    test_period_start: str
    test_period_end: str
    test_breach_rate: float
    roc_auc: float
    average_precision: float
    brier_score: float
    uncalibrated_brier_score: float
    log_loss: float
    expected_calibration_error: float
    top_decile_recall: float
    risk_population_stability_index: float
    drift_level: str
    top_feature_effects: list[FeatureEffect]


@dataclass(slots=True)
class SlaRiskModel:
    """Serializable two-stage model with a chronological Platt calibration layer."""

    base_model: Pipeline
    calibrator: LogisticRegression

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        features = prepare_features(frame)
        raw_log_odds = self.base_model.decision_function(features).reshape(-1, 1)
        return self.calibrator.predict_proba(raw_log_odds)[:, 1]


def cases_to_frame(cases: list[ServiceCase]) -> pd.DataFrame:
    """Convert domain cases to a modeling frame while retaining audit identifiers."""
    validate_cases(cases)
    return pd.DataFrame(
        [
            {
                "case_id": case.case_id,
                "opened_at": case.opened_at,
                "closed_at": case.closed_at,
                "department": str(case.department),
                "service_type": case.service_type,
                "priority": str(case.priority),
                "channel": case.channel,
                "district": case.district,
                "target_hours": case.target_hours,
            }
            for case in cases
        ]
    )


def prepare_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Build intake-time features and reject incomplete model inputs."""
    required = {"opened_at", *CATEGORICAL_FEATURES}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing prediction columns: {', '.join(missing)}")
    prepared = frame.copy()
    prepared["opened_at"] = pd.to_datetime(prepared["opened_at"], utc=True, format="ISO8601")
    prepared["district"] = prepared["district"].astype(str)
    prepared["opened_hour"] = prepared["opened_at"].dt.hour
    prepared["opened_weekday"] = prepared["opened_at"].dt.weekday
    return prepared[MODEL_FEATURES]


def _closed_labeled_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"opened_at", "closed_at", "target_hours", *CATEGORICAL_FEATURES}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing training columns: {', '.join(missing)}")
    labeled = frame.copy()
    labeled["opened_at"] = pd.to_datetime(labeled["opened_at"], utc=True, format="ISO8601")
    labeled["closed_at"] = pd.to_datetime(labeled["closed_at"], utc=True, format="ISO8601")
    labeled = labeled[labeled["closed_at"].notna()].sort_values("opened_at").reset_index(drop=True)
    resolution_hours = (labeled["closed_at"] - labeled["opened_at"]).dt.total_seconds() / 3600
    labeled["sla_breached"] = (resolution_hours > labeled["target_hours"]).astype(int)
    return labeled


def expected_calibration_error(
    labels: np.ndarray, probabilities: np.ndarray, *, bins: int = 10
) -> float:
    """Return weighted absolute calibration error across fixed probability bins."""
    if len(labels) != len(probabilities) or not len(labels):
        raise ValueError("labels and probabilities must be non-empty and equally sized")
    edges = np.linspace(0, 1, bins + 1)
    assignments = np.clip(np.digitize(probabilities, edges[1:-1]), 0, bins - 1)
    error = 0.0
    for index in range(bins):
        mask = assignments == index
        if mask.any():
            error += mask.mean() * abs(labels[mask].mean() - probabilities[mask].mean())
    return round(float(error), 4)


def probability_psi(reference: np.ndarray, current: np.ndarray, *, bins: int = 10) -> float:
    """Measure predicted-risk distribution shift with population stability index."""
    if not len(reference) or not len(current):
        raise ValueError("PSI inputs must be non-empty")
    edges = np.linspace(0, 1, bins + 1)
    reference_share = np.histogram(reference, bins=edges)[0] / len(reference)
    current_share = np.histogram(current, bins=edges)[0] / len(current)
    reference_share = np.clip(reference_share, 1e-6, None)
    current_share = np.clip(current_share, 1e-6, None)
    value = np.sum((current_share - reference_share) * np.log(current_share / reference_share))
    return round(float(value), 4)


def drift_level(psi: float) -> str:
    if psi < 0.1:
        return "stable"
    if psi < 0.25:
        return "watch"
    return "action_required"


def _top_decile_recall(labels: np.ndarray, probabilities: np.ndarray) -> float:
    positives = int(labels.sum())
    if not positives:
        return 0.0
    selected = max(1, ceil(len(labels) * 0.1))
    top_indices = np.argsort(probabilities)[-selected:]
    return round(float(labels[top_indices].sum() / positives), 4)


def _feature_effects(model: Pipeline, *, limit: int = 12) -> list[FeatureEffect]:
    preprocessor: ColumnTransformer = model.named_steps["preprocess"]
    classifier: LogisticRegression = model.named_steps["classifier"]
    names = preprocessor.get_feature_names_out()
    coefficients = classifier.coef_[0]
    ranked = sorted(
        zip(names, coefficients, strict=True), key=lambda item: abs(item[1]), reverse=True
    )
    return [
        FeatureEffect(
            feature=name.replace("categorical__", "").replace("numeric__", ""),
            coefficient=round(float(coefficient), 4),
            direction="higher_risk" if coefficient > 0 else "lower_risk",
        )
        for name, coefficient in ranked[:limit]
    ]


def train_sla_risk_model_frame(
    frame: pd.DataFrame,
) -> tuple[SlaRiskModel, PredictionReport]:
    """Fit, calibrate, and evaluate on strictly ordered chronological partitions."""
    labeled = _closed_labeled_frame(frame)
    if len(labeled) < 300:
        raise ValueError("SLA prediction requires at least 300 closed cases")
    training_end = int(len(labeled) * 0.6)
    calibration_end = int(len(labeled) * 0.8)
    training = labeled.iloc[:training_end]
    calibration = labeled.iloc[training_end:calibration_end]
    test = labeled.iloc[calibration_end:]
    for name, partition in (
        ("training", training),
        ("calibration", calibration),
        ("test", test),
    ):
        if partition["sla_breached"].nunique() < 2:
            raise ValueError(f"{name} partition must contain both outcomes")

    preprocessor = ColumnTransformer(
        [
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                CATEGORICAL_FEATURES,
            ),
            ("numeric", StandardScaler(), NUMERIC_FEATURES),
        ]
    )
    base_model = Pipeline(
        [
            ("preprocess", preprocessor),
            (
                "classifier",
                LogisticRegression(class_weight="balanced", max_iter=1_000, random_state=42),
            ),
        ]
    )
    base_model.fit(prepare_features(training), training["sla_breached"])

    calibration_log_odds = base_model.decision_function(prepare_features(calibration)).reshape(
        -1, 1
    )
    calibrator = LogisticRegression(max_iter=1_000, random_state=42)
    calibrator.fit(calibration_log_odds, calibration["sla_breached"])
    model = SlaRiskModel(base_model=base_model, calibrator=calibrator)

    labels = test["sla_breached"].to_numpy()
    uncalibrated = base_model.predict_proba(prepare_features(test))[:, 1]
    probabilities = model.predict_proba(test)
    training_probabilities = model.predict_proba(training)
    psi = probability_psi(training_probabilities, probabilities)
    report = PredictionReport(
        closed_cases=len(labeled),
        training_cases=len(training),
        calibration_cases=len(calibration),
        test_cases=len(test),
        training_period_start=training["opened_at"].min().isoformat(),
        training_period_end=training["opened_at"].max().isoformat(),
        test_period_start=test["opened_at"].min().isoformat(),
        test_period_end=test["opened_at"].max().isoformat(),
        test_breach_rate=round(float(labels.mean()), 4),
        roc_auc=round(float(roc_auc_score(labels, probabilities)), 4),
        average_precision=round(float(average_precision_score(labels, probabilities)), 4),
        brier_score=round(float(brier_score_loss(labels, probabilities)), 4),
        uncalibrated_brier_score=round(float(brier_score_loss(labels, uncalibrated)), 4),
        log_loss=round(float(log_loss(labels, probabilities)), 4),
        expected_calibration_error=expected_calibration_error(labels, probabilities),
        top_decile_recall=_top_decile_recall(labels, probabilities),
        risk_population_stability_index=psi,
        drift_level=drift_level(psi),
        top_feature_effects=_feature_effects(base_model),
    )
    return model, report


def train_sla_risk_model(cases: list[ServiceCase]) -> tuple[SlaRiskModel, PredictionReport]:
    return train_sla_risk_model_frame(cases_to_frame(cases))


def score_open_cases_frame(
    frame: pd.DataFrame, model: SlaRiskModel, *, limit: int | None = None
) -> pd.DataFrame:
    """Rank open cases by calibrated breach probability for operational triage."""
    required = {"case_id", "closed_at", "opened_at", "department", "priority", "district"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing scoring columns: {', '.join(missing)}")
    open_cases = frame[pd.isna(frame["closed_at"])].copy()
    if open_cases.empty:
        return pd.DataFrame(
            columns=[
                "case_id",
                "department",
                "priority",
                "district",
                "opened_at",
                "risk_probability",
                "risk_band",
            ]
        )
    open_cases["risk_probability"] = model.predict_proba(open_cases)
    open_cases["risk_band"] = pd.cut(
        open_cases["risk_probability"],
        bins=[-0.001, 0.35, 0.65, 1.0],
        labels=["low", "medium", "high"],
    ).astype(str)
    ranked = open_cases.sort_values("risk_probability", ascending=False)
    if limit is not None:
        ranked = ranked.head(limit)
    return ranked[
        [
            "case_id",
            "department",
            "priority",
            "district",
            "opened_at",
            "risk_probability",
            "risk_band",
        ]
    ].reset_index(drop=True)
