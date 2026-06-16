import json
import logging
import threading
import paho.mqtt.client as mqtt
from typing import Any, Dict

logger = logging.getLogger("mqtt")

class MQTTPublisher:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MQTTPublisher, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, host: str = "localhost", port: int = 1883):
        if getattr(self, "_initialized", False):
            return
            
        self.host = host
        self.port = port
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self._connected = False
        self._lock = threading.Lock()
        
        try:
            self.client.connect(self.host, self.port, 60)
            self.client.loop_start()
            self._initialized = True
        except Exception as e:
            logger.error("Failed to connect to MQTT broker at %s:%d: %s", self.host, self.port, e)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            logger.info("Connected to MQTT broker at %s:%d", self.host, self.port)
        else:
            logger.error("MQTT connection failed with code %s", rc)

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        logger.warning("Disconnected from MQTT broker (code %s)", rc)

    def publish_event(self, event_type: str, ticker: str, payload: Dict[str, Any] = None):
        if not self._connected:
            logger.warning("Cannot publish event %s, not connected to MQTT broker", event_type)
            return

        topic = "agents/stock-data/events"
        data = {
            "event": event_type,
            "ticker": ticker
        }
        if payload:
            data.update(payload)

        try:
            msg = json.dumps(data)
            self.client.publish(topic, msg, qos=1)
            logger.debug("Published MQTT event to %s: %s", topic, msg)
        except Exception as e:
            logger.error("Error publishing MQTT event: %s", e)

# Global publisher instance
_publisher = None

def get_publisher() -> MQTTPublisher:
    global _publisher
    if _publisher is None:
        _publisher = MQTTPublisher()
    return _publisher

def publish_download_complete(ticker: str, timeframe: str):
    get_publisher().publish_event("download_complete", ticker, {"timeframe": timeframe})

def publish_download_failed(ticker: str, timeframe: str, reason: str = ""):
    get_publisher().publish_event("download_failed", ticker, {"timeframe": timeframe, "reason": reason})
