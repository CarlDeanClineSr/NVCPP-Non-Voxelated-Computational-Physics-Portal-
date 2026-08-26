import json
from pathlib import Path

import pytest

from sources.solar1.validate_contract import (
    ContractValidationError,
    load_contract_or_raise,
    validate_contract,
    validate_contract_or_raise,
)


CONTRACT = Path("config/solar1_mag_contract.v1.json")


def test_committed_contract_is_authoritative_and_valid():
    data = load_contract_or_raise(CONTRACT)
    assert data["status"] == "FROZEN_VERIFIED"
    assert data["quality"]["quality_parameter_available"] is False


def test_validator_return_value_is_enforced():
    data = json.loads(CONTRACT.read_text())
    data["vector"]["units"] = "tesla"
    assert any("vector.units" in error for error in validate_contract(data))
    with pytest.raises(ContractValidationError):
        validate_contract_or_raise(data)


def test_no_provider_quality_parameter_is_allowed_when_basis_is_explicit():
    data = json.loads(CONTRACT.read_text())
    assert validate_contract(data) == []
    data["quality"]["quality_basis"] = []
    assert any("quality_basis" in error for error in validate_contract(data))


def test_clipping_cannot_be_enabled():
    data = json.loads(CONTRACT.read_text())
    data["physics"]["clipping_allowed"] = True
    with pytest.raises(ContractValidationError):
        validate_contract_or_raise(data)
