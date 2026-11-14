import pytest

import mqtt_test_broker  # liegt neben dieser conftest.py


@pytest.fixture(scope="session")
def mqtt_broker():
    """Ein Test-Broker für alle MQTT-Tests eines Laufs."""
    broker = mqtt_test_broker.start()
    try:
        yield broker
    finally:
        mqtt_test_broker.stop()
