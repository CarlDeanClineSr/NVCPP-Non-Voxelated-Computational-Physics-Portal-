import hashlib, json
from collections import Counter
from pathlib import Path
REGISTRY = Path("config/gannon_holdout_v2.registry.json")
INVENTORY = Path("provenance/gannon_holdout_v2_registry/FROZEN_INVENTORY.json")
def test_amended_registry_is_frozen_and_unequal_by_design():
    data = json.loads(REGISTRY.read_text())
    assert data["status"] == "FROZEN_BEFORE_HOLDOUT_MAG_RETRIEVAL"
    assert Counter(x["class"] for x in data["intervals"]) == {
        "QUIET_SOLAR_WIND": 12,
        "MODERATE_VARIABILITY": 12,
        "ISOLATED_SHOCK_OR_SHEATH": 12,
        "COMPLEX_INTERACTING_EJECTA": 7,
    }
    assert len(data["intervals"]) == 43
def test_registry_firewall_and_denominator_policy():
    data = json.loads(REGISTRY.read_text())
    assert all(data["selection_firewall"][k] is False for k in ("spacecraft_mag_retrieved", "mag_values_inspected", "gate_outputs_inspected", "clustering_outputs_inspected"))
    assert all(x["failure_policy"] == "INCOMPLETE_MULTIPOINT_RETAIN_IN_DENOMINATOR" for x in data["intervals"])
    assert all(x["replacement_after_scoring_allowed"] is False for x in data["intervals"])
def test_registry_hash_reproduces():
    data = json.loads(REGISTRY.read_text())
    expected = data.pop("registry_content_sha256")
    data.pop("created_utc", None)
    assert hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == expected
def test_inventory_hashes_committed_files():
    inv = json.loads(INVENTORY.read_text())
    assert inv["class_denominators"]["COMPLEX_INTERACTING_EJECTA"] == 7
    assert inv["spacecraft_mag_retrieved"] is False
    for item in inv["files"]:
        p = Path(item["path"])
        assert hashlib.sha256(p.read_bytes()).hexdigest() == item["sha256"]
