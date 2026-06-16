import json
import logging
import asyncio
from paho.mqtt.client import Client, MQTTMessage
from typing import Any

from models import DownloadPriority, DownloadRequest, IFailedTickerStore, IPriorityQueue, ITickerResolver, IConfigLoader

logger = logging.getLogger("mqtt_listener")

class MQTTListener:
    def __init__(
        self,
        host: str,
        port: int,
        queue: IPriorityQueue,
        resolver: ITickerResolver,
        failed_store: IFailedTickerStore,
        config: IConfigLoader
    ):
        self.host = host
        self.port = port
        self.queue = queue
        self.resolver = resolver
        self.failed_store = failed_store
        self.config = config
        
        self.client = Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._connected = False

    def _on_connect(self, client: Client, userdata: Any, flags: Any, rc: int) -> None:
        if rc == 0:
            self._connected = True
            logger.info("✅ Connected to MQTT broker at %s:%d", self.host, self.port)
            self.client.subscribe("agents/stock-data/commands")
            logger.info("📡 Subscribed to agents/stock-data/commands")
        else:
            logger.error("❌ MQTT connection failed with code %s", rc)

    def _on_message(self, client: Client, userdata: Any, msg: MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            action = payload.get("action")
            
            if action == "request_download":
                ticker = payload.get("ticker", "").strip().upper()
                if not ticker:
                    return
                
                logger.info("📥 MQTT request_download received for %s", ticker)
                
                # Check blacklist
                if self.failed_store.is_blacklisted(ticker):
                    logger.info("ℹ️ MQTT request for blacklisted ticker %s — removing from blacklist to allow retry", ticker)
                    self.failed_store.remove(ticker)
                    
                # Check mapping to SKIP
                if self.resolver.is_ignored(ticker):
                    logger.warning("❌ Rejected MQTT download for %s (mapped to SKIP)", ticker)
                    return
                    
                # Resolve contract (which talks to IBKR if unknown)
                # Note: resolver.resolve() is synchronous but uses cached mapping or blocks for discovery.
                contract = self.resolver.resolve(ticker)
                
                timeframes = self.config.get_timeframes_for_ticker(ticker)
                
                daily_tfs = [tf for tf in timeframes if tf == "1D"]
                other_tfs  = [tf for tf in timeframes if tf != "1D"]
                ordered_tfs = daily_tfs + other_tfs
                
                for tf in ordered_tfs:
                    req = DownloadRequest(
                        ticker=ticker,
                        timeframe=tf,
                        priority=DownloadPriority.API,  # High priority like API
                        contract=contract,
                    )
                    self.queue.enqueue(req)
                    
                logger.info("✅ MQTT request accepted: enqueued %s for timeframes %s", ticker, ordered_tfs)
                
        except json.JSONDecodeError:
            logger.warning("Invalid JSON received on MQTT: %s", msg.payload)
        except Exception as e:
            logger.error("Error processing MQTT message: %s", e)

    def start(self) -> None:
        try:
            self.client.connect(self.host, self.port, 60)
            self.client.loop_start()
        except Exception as e:
            logger.error("Failed to connect to MQTT broker at %s:%d: %s", self.host, self.port, e)

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()
