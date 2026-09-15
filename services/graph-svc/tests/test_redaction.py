"""Inference-boundary redaction -- stories S11.4.1/S11.4.2. Pure functions, no
Postgres; see test_integration_mender.py for the real, end-to-end `assemble_repair_
context` check, and test_integration_gateway.py for the real, end-to-end pattern-
redaction-before-the-real-provider-call check."""

from __future__ import annotations

import hashlib

from astra_graph.redaction import (
    bucket_measure_value,
    hash_key_value,
    redact_data_like_literals,
    redact_failing_cell,
)


class TestHashKeyValue:
    def test_the_same_value_hashes_identically_every_time(self) -> None:
        assert hash_key_value("Desk-0") == hash_key_value("Desk-0")

    def test_different_values_hash_differently(self) -> None:
        assert hash_key_value("Desk-0") != hash_key_value("Desk-1")

    def test_the_real_value_never_appears_in_its_own_hash(self) -> None:
        assert "Desk-0" not in hash_key_value("Desk-0")

    def test_matches_a_real_sha256_digest(self) -> None:
        expected = f"sha256:{hashlib.sha256(b'Desk-0').hexdigest()[:16]}"
        assert hash_key_value("Desk-0") == expected


class TestBucketMeasureValue:
    def test_none_buckets_to_null(self) -> None:
        assert bucket_measure_value(None) == "null"

    def test_zero_buckets_to_zero(self) -> None:
        assert bucket_measure_value(0) == "0"
        assert bucket_measure_value(0.0) == "0"

    def test_a_positive_value_buckets_to_its_own_sign_and_order(self) -> None:
        assert bucket_measure_value(123.45) == "+1e2"
        assert bucket_measure_value(1.0) == "+1e0"
        assert bucket_measure_value(0.05) == "+1e-2"

    def test_a_negative_value_buckets_to_its_own_sign_and_order(self) -> None:
        assert bucket_measure_value(-999.0) == "-1e2"

    def test_a_non_numeric_value_never_raises(self) -> None:
        assert bucket_measure_value("not a number") == "non-numeric"
        assert bucket_measure_value(object()) == "non-numeric"

    def test_the_real_value_never_appears_in_its_own_bucket(self) -> None:
        assert "123.45" not in bucket_measure_value(123.45)


class TestRedactFailingCell:
    def test_grain_key_elements_are_each_hashed(self) -> None:
        cell = {"grain_key": ["Desk-0", "2027-01-01"], "measure": "MarginCalc"}
        redacted = redact_failing_cell(cell)
        assert redacted["grain_key"] == [hash_key_value("Desk-0"), hash_key_value("2027-01-01")]

    def test_expected_and_candidate_are_bucketed(self) -> None:
        cell = {"expected": 100.0, "candidate": None}
        redacted = redact_failing_cell(cell)
        assert redacted["expected"] == "+1e2"
        assert redacted["candidate"] == "null"

    def test_a_name_field_passes_through_unchanged(self) -> None:
        cell = {"measure": "MarginCalc", "kind": "numeric", "case_ref": "case_1"}
        redacted = redact_failing_cell(cell)
        assert redacted == cell

    def test_a_cell_with_no_grain_key_is_left_alone(self) -> None:
        cell = {"row": {"Desk": "Desk-0"}, "expected": 5.0}
        redacted = redact_failing_cell(cell)
        assert redacted["row"] == {"Desk": "Desk-0"}
        assert redacted["expected"] == "+1e0"

    def test_the_original_cell_is_never_mutated(self) -> None:
        cell = {"grain_key": ["Desk-0"], "expected": 1.0}
        redact_failing_cell(cell)
        assert cell["grain_key"] == ["Desk-0"]
        assert cell["expected"] == 1.0


class TestRedactDataLikeLiterals:
    def test_an_email_is_redacted(self) -> None:
        redacted, count = redact_data_like_literals("contact jane.doe@example.com now")
        assert redacted == "contact [REDACTED:EMAIL] now"
        assert count == 1

    def test_an_account_number_shaped_digit_group_is_redacted(self) -> None:
        redacted, count = redact_data_like_literals("card 4111 2222 3333 4444 on file")
        assert "[REDACTED:ACCOUNT_NUMBER]" in redacted
        assert "4111" not in redacted
        assert count == 1

    def test_a_long_bare_numeric_literal_is_redacted(self) -> None:
        redacted, count = redact_data_like_literals("id 123456789 seen")
        assert redacted == "id [REDACTED:LONG_NUMERIC_LITERAL] seen"
        assert count == 1

    def test_a_short_number_is_left_alone(self) -> None:
        redacted, count = redact_data_like_literals("row count 12345")
        assert redacted == "row count 12345"
        assert count == 0

    def test_clean_text_is_returned_unchanged_with_a_zero_count(self) -> None:
        redacted, count = redact_data_like_literals("SUM([Margin]) / SUM([Revenue])")
        assert redacted == "SUM([Margin]) / SUM([Revenue])"
        assert count == 0

    def test_multiple_matches_in_one_string_are_all_redacted_and_counted(self) -> None:
        redacted, count = redact_data_like_literals("a@b.com and c@d.com")
        assert redacted == "[REDACTED:EMAIL] and [REDACTED:EMAIL]"
        assert count == 2

    def test_walks_nested_dicts_and_lists(self) -> None:
        payload = {
            "top": "clean",
            "nested": {"deep": ["safe", "leak@example.com", {"deeper": "999888777"}]},
        }
        redacted, count = redact_data_like_literals(payload)
        assert redacted["top"] == "clean"
        assert redacted["nested"]["deep"][0] == "safe"
        assert redacted["nested"]["deep"][1] == "[REDACTED:EMAIL]"
        assert redacted["nested"]["deep"][2]["deeper"] == "[REDACTED:LONG_NUMERIC_LITERAL]"
        assert count == 2

    def test_non_string_leaves_pass_through_unchanged(self) -> None:
        payload = {"n": 42, "b": True, "none": None, "f": 3.14}
        redacted, count = redact_data_like_literals(payload)
        assert redacted == payload
        assert count == 0

    def test_a_tuple_is_preserved_as_a_tuple(self) -> None:
        redacted, count = redact_data_like_literals(("clean", "a@b.com"))
        assert redacted == ("clean", "[REDACTED:EMAIL]")
        assert count == 1

    def test_the_original_payload_is_never_mutated(self) -> None:
        payload = {"formula": "IF [x] = \"a@b.com\" THEN 1"}
        redact_data_like_literals(payload)
        assert payload["formula"] == "IF [x] = \"a@b.com\" THEN 1"

    def test_dict_keys_are_never_touched_only_values(self) -> None:
        payload = {"a@b.com": "clean value"}
        redacted, count = redact_data_like_literals(payload)
        assert "a@b.com" in redacted
        assert redacted["a@b.com"] == "clean value"
        assert count == 0
