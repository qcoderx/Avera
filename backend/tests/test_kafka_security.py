import pytest

from avera.events.bus import KafkaRedpandaEventBus


def test_kafka_bus_defaults_to_local_plaintext_without_auth_options():
    bus = KafkaRedpandaEventBus("127.0.0.1:9092")

    assert bus._client_options() == {"security_protocol": "PLAINTEXT"}


def test_kafka_bus_supports_authenticated_tls_for_managed_brokers(monkeypatch):
    ssl_context = object()
    monkeypatch.setattr("avera.events.bus.ssl.create_default_context", lambda **kwargs: ssl_context)
    bus = KafkaRedpandaEventBus(
        "broker.example:1234",
        security_protocol="sasl_ssl",
        sasl_mechanism="SCRAM-SHA-256",
        sasl_username="avera",
        sasl_password="test-secret",
        ca_file="/etc/avera/kafka-ca.pem",
    )

    assert bus._client_options() == {
        "security_protocol": "SASL_SSL",
        "sasl_mechanism": "SCRAM-SHA-256",
        "sasl_plain_username": "avera",
        "sasl_plain_password": "test-secret",
        "ssl_context": ssl_context,
    }


@pytest.mark.parametrize(
    "options",
    [
        {"security_protocol": "SASL_SSL"},
        {"security_protocol": "SASL_SSL", "sasl_mechanism": "SCRAM-SHA-256", "sasl_username": "avera"},
        {"security_protocol": "SASL_SSL", "sasl_mechanism": "SCRAM-SHA-256", "sasl_username": "avera", "sasl_password": "test-secret"},
        {"security_protocol": "SSL"},
    ],
)
def test_kafka_bus_rejects_incomplete_or_unsupported_security(options):
    bus = KafkaRedpandaEventBus("broker.example:1234", **options)

    with pytest.raises(ValueError):
        bus._client_options()
