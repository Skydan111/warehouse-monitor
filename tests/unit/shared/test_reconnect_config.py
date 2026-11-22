"""Wiederverbindung der MQTT-Clients (shared.comm.mqtt_reconnect)."""
import paho.mqtt.client as mqtt

from shared.comm.mqtt_reconnect import RECONNECT_MAX_DELAY_S, RECONNECT_MIN_DELAY_S, configure_reconnect


def test_reconnect_delay_is_1_to_30_seconds():
    c = mqtt.Client(client_id="t")
    configure_reconnect(c, "[t]")
    assert (RECONNECT_MIN_DELAY_S, RECONNECT_MAX_DELAY_S) == (1, 30)
    assert (c._reconnect_min_delay, c._reconnect_max_delay) == (1, 30)


def test_unexpected_disconnect_is_logged_planned_is_not(capsys):
    c = mqtt.Client(client_id="t")
    configure_reconnect(c, "[t]")
    c.on_disconnect(c, None, 0)
    assert capsys.readouterr().err == ""
    c.on_disconnect(c, None, 7)
    assert "Verbindung zum Broker verloren (rc=7)" in capsys.readouterr().err


def test_connect_fail_is_logged(capsys):
    c = mqtt.Client(client_id="t")
    configure_reconnect(c, "[t]")
    c.on_connect_fail(c, None)
    assert "Broker nicht erreichbar" in capsys.readouterr().err
