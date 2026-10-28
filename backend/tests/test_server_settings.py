import pytest


def test_default_binding_is_loopback_and_does_not_require_secrets():
    from server_settings import load_server_settings
    settings = load_server_settings({})
    assert settings.bind_host == "127.0.0.1"
    assert settings.security.password == ""
    assert settings.security.public_get_paths == ("/", "/assets/")


@pytest.mark.parametrize("env", [
    {"RADIO_BIND_HOST": "0.0.0.0"},
    {"RADIO_PUBLIC_ORIGIN": "https://radio.test"},
    {"RADIO_BIND_HOST": "0.0.0.0", "RADIO_PASSWORD": "fixture", "RADIO_AUTH_SECRET": "a" * 40},
])
def test_network_exposure_without_credentials_or_transport_opt_in_fails_closed(env):
    from server_settings import load_server_settings
    with pytest.raises(ValueError):
        load_server_settings(env)


def test_https_reverse_proxy_is_explicit_and_credentials_are_not_repr_visible():
    from server_settings import load_server_settings
    settings = load_server_settings({
        "RADIO_PASSWORD": "fixture-private-password", "RADIO_AUTH_SECRET": "fixture-private-signing-secret-1234567890",
        "RADIO_PUBLIC_ORIGIN": "https://radio.test",
    })
    assert settings.bind_host == "127.0.0.1"
    assert "radio.test" in settings.security.allowed_hosts
    assert settings.security.public_origin == "https://radio.test"
    assert "fixture-private" not in repr(settings)


def test_plain_lan_http_requires_explicit_opt_in():
    from server_settings import load_server_settings
    settings = load_server_settings({
        "RADIO_BIND_HOST": "0.0.0.0", "RADIO_PASSWORD": "fixture", "RADIO_AUTH_SECRET": "a" * 40,
        "RADIO_ALLOW_INSECURE_LAN": "1", "RADIO_ALLOWED_HOSTS": "localhost,192.168.1.20",
    })
    assert settings.security.allowed_hosts == ("localhost", "192.168.1.20")
