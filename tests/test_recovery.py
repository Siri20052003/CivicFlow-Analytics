import json

import pytest

from civicflow.recovery import (
    RecoveryPolicy,
    load_recovery_policy,
    validate_rehearsal_database_name,
)


def test_recovery_policy_is_strict_and_bounded(tmp_path) -> None:
    valid = tmp_path / "valid.json"
    valid.write_text(
        json.dumps(
            {
                "maximum_recovery_seconds": 120,
                "maximum_recovery_point_age_seconds": 300,
                "required_migration_version": 1,
            }
        )
    )
    assert load_recovery_policy(valid) == RecoveryPolicy(120, 300, 1)
    invalid = tmp_path / "invalid.json"
    invalid.write_text(
        json.dumps(
            {
                "maximum_recovery_seconds": 120,
                "maximum_recovery_point_age_seconds": 300,
                "required_migration_version": 1,
                "unreviewed": True,
            }
        )
    )

    with pytest.raises(ValueError, match="exactly"):
        load_recovery_policy(invalid)


@pytest.mark.parametrize(
    "name",
    [
        "production",
        "civicflow_rehearsal_",
        "civicflow_rehearsal_UNSAFE",
        "civicflow_rehearsal_valid;drop database production",
        "other_rehearsal_20261004",
    ],
)
def test_rehearsal_database_safety_namespace_rejects_unsafe_names(name) -> None:
    with pytest.raises(ValueError, match="safety namespace"):
        validate_rehearsal_database_name(name)


def test_rehearsal_database_safety_namespace_accepts_generated_name() -> None:
    validate_rehearsal_database_name("civicflow_rehearsal_20261004_130000")
