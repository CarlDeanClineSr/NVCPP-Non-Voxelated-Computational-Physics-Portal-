import copy
import unittest

from sources.solar1.validate_contract import validate_contract


class Solar1ContractTests(unittest.TestCase):
    def valid_contract(self):
        component = {
            "parameter_id": "placeholder",
            "label": "component",
            "fill_values": [-1.0e31],
            "valid_min": -250.0,
            "valid_max": 250.0,
            "scale_factor": 1.0,
            "offset": 0.0,
        }
        return {
            "contract_version": "1.0.0",
            "status": "FROZEN_VERIFIED",
            "mission": "SOLAR-1",
            "provider": "NOAA/NCEI",
            "instrument": "MAG",
            "source": {
                "api_base": "https://www.ncei.noaa.gov/cloud-access/space-weather-portal/api/v1",
                "api_build": "v20260729",
                "product_id": "sci_mag-l3_solar1",
                "product_title": "SOLAR-1 MAG Level 3 science-quality",
                "product_level": "L3",
                "quality_class": "science-quality",
                "hapi_dataset_id": "verified-dataset-id",
                "availability_start_utc": "2026-04-01T00:00:00Z",
                "availability_end_utc": None,
                "metadata_sha256": ["a" * 64],
            },
            "time": {
                "parameter_id": "Time",
                "format": "ISO-8601",
                "timezone": "UTC",
                "timestamp_semantics": "interval-center",
                "leap_second_policy": "provider-declared",
                "duplicate_policy": "reject-unless-identical",
            },
            "vector": {
                "coordinate_frame": "GSE",
                "units": "nT",
                "components": {
                    "x": {**component, "parameter_id": "BX", "label": "Bx"},
                    "y": {**component, "parameter_id": "BY", "label": "By"},
                    "z": {**component, "parameter_id": "BZ", "label": "Bz"},
                },
            },
            "cadence": {
                "native_iso8601": "PT1M",
                "canonical_iso8601": "PT1M",
                "official_one_minute_product": True,
                "minimum_native_coverage_fraction": None,
                "aggregation": "official-provider-average",
            },
            "quality": {
                "parameter_ids": ["QUALITY_FLAG"],
                "reject_rules": ["reject provider-invalid records"],
                "warning_rules": [],
                "calibration_state_parameters": [],
            },
            "physics": {
                "protocol_id": "CLINE-L1-B24M-TRAIL-v1",
                "baseline": "prior-only trailing 24-hour median",
                "minimum_baseline_coverage_fraction": 0.95,
                "pre_roll_hours": 24,
                "clipping_allowed": False,
                "science_computation_enabled": True,
            },
        }

    def test_complete_verified_contract_passes(self):
        self.assertEqual(validate_contract(self.valid_contract()), [])

    def test_locked_contract_cannot_enter_science(self):
        data = self.valid_contract()
        data["status"] = "DISCOVERY_LOCKED"
        data["physics"]["science_computation_enabled"] = False
        errors = validate_contract(data)
        self.assertTrue(any("not FROZEN_VERIFIED" in item for item in errors))

    def test_wrong_units_fail(self):
        data = self.valid_contract()
        data["vector"]["units"] = "tesla"
        self.assertTrue(any("nanotesla" in item for item in validate_contract(data)))

    def test_duplicate_component_ids_fail(self):
        data = self.valid_contract()
        data["vector"]["components"]["z"]["parameter_id"] = "BX"
        self.assertTrue(any("distinct" in item for item in validate_contract(data)))

    def test_clipping_is_forbidden(self):
        data = self.valid_contract()
        data["physics"]["clipping_allowed"] = True
        self.assertTrue(any("clipping_allowed" in item for item in validate_contract(data)))

    def test_metadata_hash_is_required(self):
        data = self.valid_contract()
        data["source"]["metadata_sha256"] = []
        self.assertTrue(any("metadata_sha256" in item for item in validate_contract(data)))


if __name__ == "__main__":
    unittest.main()
