from secondshift.runner_protocol.credentials import key_fingerprint, same_key
from secondshift.runner_protocol.tests.keys import public_key_pem


def test_fingerprints_compare_keys_not_text() -> None:
    key, other = public_key_pem(), public_key_pem()

    assert same_key(key, key.replace("\n", "\r\n"))
    assert not same_key(key, other)
    assert key_fingerprint(key).startswith("SHA256:")


def test_a_value_that_is_not_a_key_matches_nothing() -> None:
    assert not same_key("not a key", public_key_pem())
