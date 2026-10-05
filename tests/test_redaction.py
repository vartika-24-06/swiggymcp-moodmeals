import pytest

from moodmeals.core.redaction import redact_payload, redact_text

ADDRESS = "Flat 12B, Sample Heights, Demo Nagar, Testville"


@pytest.mark.parametrize(
    "text",
    [
        "call 9876543210 now",
        "call +91 98765 43210 now",
        "call +91-98765-43210 now",
        "call 98765 43210 now",
        "call 0 98765 43210 now",
    ],
)
def test_phone_numbers_are_masked(text):
    out = redact_text(text)
    assert "98765" not in out and "43210" not in out
    assert "<phone>" in out


def test_email_and_secrets_are_masked():
    assert "<email>" in redact_text("mail me at someone@example.com")
    for key in (
        "sk-abcdefghijklmnop1234",
        "AIzaSyA1234567890abcdefghijk",
        "gsk_abcdefghijklmnop1234",
        "Bearer abcdefghijklmnop12345",
    ):
        out = redact_text(f"my key is {key}")
        assert key not in out and "<secret>" in out


def test_long_digit_runs_such_as_pin_codes_are_masked():
    assert redact_text("pin 560001 done") == "pin <digits> done"


def test_known_address_text_is_removed_case_insensitively():
    out = redact_text(f"deliver to {ADDRESS.upper()} please", known_sensitive=[ADDRESS])
    assert "SAMPLE HEIGHTS" not in out and "<address>" in out


def test_buy_again_badges_are_removed():
    assert "Buy again" not in redact_text("Paneer 200 g - Buy again")


@pytest.mark.parametrize(
    "text",
    [
        "₹400 for two",
        "10-20 MINS",
        "5.1K+ ratings",
        "₹1,299",
        "r_101",
        "ETA 25 mins, rating 4.3",
        "2026-10-04",
        "qty 3 of 12",
        "₹99.50",
    ],
)
def test_ordinary_values_are_left_alone(text):
    assert redact_text(text) == text


def test_sensitive_keys_are_dropped_whole():
    payload = {
        "restaurant": "Sample Kitchen",
        "address": ADDRESS,
        "addressLine1": "Flat 12B",
        "Phone Number": "9876543210",
        "lat": 12.34,
        "access_token": "abc",
        "nested": [{"customer_name": "Someone", "total": 120}],
    }
    out = redact_payload(payload)
    assert out["restaurant"] == "Sample Kitchen"
    for k in ("address", "addressLine1", "Phone Number", "lat", "access_token"):
        assert out[k] == "[redacted]"
    assert out["nested"] == [{"customer_name": "[redacted]", "total": 120}]


def test_redaction_does_not_mutate_input():
    payload = {"note": "call 9876543210", "address": ADDRESS}
    redact_payload(payload)
    assert payload == {"note": "call 9876543210", "address": ADDRESS}


def test_non_json_values_become_redacted_strings():
    class Odd:
        def __str__(self):
            return "9876543210"

    assert redact_payload({"x": Odd()}) == {"x": "<phone>"}


def test_catalogue_ids_survive_in_event_payloads_but_free_text_digits_do_not():
    out = redact_payload({"entity_id": "61093009", "name": "call 98765432101 now"})
    assert out["entity_id"] == "61093009"
    assert "98765432101" not in out["name"]
    assert redact_payload({"entity_id": "61093009"}, ["61093009"])["entity_id"] != "61093009"
