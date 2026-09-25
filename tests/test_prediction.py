from __future__ import annotations

from datetime import UTC, datetime

import joblib
import numpy as np
import pandas as pd
import pytest

from civicflow.prediction import (
    LEAKAGE_FIELDS,
    MODEL_FEATURES,
    cases_to_frame,
    drift_level,
    expected_calibration_error,
    probability_psi,
    score_open_cases_frame,
    train_sla_risk_model,
)
from civicflow.synthetic import generate_cases


@pytest.fixture(scope="module")
def trained_model():
    cases = generate_cases(
        5_000,
        seed=20260925,
        as_of=datetime(2026, 9, 25, tzinfo=UTC),
    )
    model, report = train_sla_risk_model(cases)
    return cases, model, report


def test_prediction_is_chronological_calibrated_and_leakage_safe(trained_model) -> None:
    cases, _, report = trained_model
    closed_cases = sum(case.closed_at is not None for case in cases)

    assert report.closed_cases == closed_cases
    assert report.training_cases + report.calibration_cases + report.test_cases == closed_cases
    assert report.training_period_end < report.test_period_start
    assert 0.5 <= report.roc_auc <= 1
    assert 0 <= report.average_precision <= 1
    assert 0 <= report.brier_score <= 1
    assert 0 <= report.expected_calibration_error <= 1
    assert 0 <= report.top_decile_recall <= 1
    assert report.drift_level in {"stable", "watch", "action_required"}
    assert report.top_feature_effects
    assert set(MODEL_FEATURES).isdisjoint(LEAKAGE_FIELDS)


def test_open_case_scores_are_ranked_and_serializable(trained_model, tmp_path) -> None:
    cases, model, _ = trained_model
    frame = cases_to_frame(cases)
    scores = score_open_cases_frame(frame, model, limit=25)

    assert len(scores) == 25
    assert scores["risk_probability"].between(0, 1).all()
    assert scores["risk_probability"].is_monotonic_decreasing
    assert set(scores["risk_band"]) <= {"low", "medium", "high"}
    assert frame.loc[frame["case_id"].isin(scores["case_id"]), "closed_at"].isna().all()

    destination = tmp_path / "model.joblib"
    joblib.dump(model, destination)
    restored = joblib.load(destination)
    np.testing.assert_allclose(
        restored.predict_proba(frame.head(10)), model.predict_proba(frame.head(10))
    )


def test_calibration_and_drift_helpers() -> None:
    labels = np.array([0, 0, 1, 1])
    probabilities = np.array([0.1, 0.2, 0.8, 0.9])
    assert expected_calibration_error(labels, probabilities, bins=2) == 0.15
    assert probability_psi(probabilities, probabilities, bins=5) == 0
    shifted = probability_psi(probabilities, np.array([0.8, 0.85, 0.9, 0.95]), bins=5)
    assert shifted > 0.25
    assert drift_level(shifted) == "action_required"


def test_training_rejects_an_insufficient_history() -> None:
    cases = generate_cases(200, seed=12, as_of=datetime(2026, 9, 25, tzinfo=UTC))
    with pytest.raises(ValueError, match="at least 300 closed cases"):
        train_sla_risk_model(cases)


def test_feature_builder_rejects_missing_intake_fields(trained_model) -> None:
    cases, model, _ = trained_model
    incomplete = pd.DataFrame({"opened_at": [cases[0].opened_at]})
    with pytest.raises(ValueError, match="missing prediction columns"):
        model.predict_proba(incomplete)
