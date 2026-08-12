"""
Tiny RabbitMQ helper shared (by copy) across the three POC services.

Why a copy in every service?
  Each service is built into its own Docker image. Copying ~150 lines keeps the
  Dockerfiles trivial and makes each service readable on its own. A real system
  would publish this as a small internal library.

Design notes for this POC:
  * One topic exchange: its.rms.events
  * Publishers send JSON envelopes with a routing key (e.g. patient.registered)
  * Consumers bind a durable queue to the exchange with routing key patterns
  * Startup is retried in a loop, so a service can boot before RabbitMQ is ready
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Iterable

import aio_pika

EXCHANGE_NAME = os.getenv("RABBITMQ_EXCHANGE", "its.rms.events")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

logger = logging.getLogger("eventbus")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_envelope(event_type: str, source: str, data: dict) -> dict:
    """Every event on the bus uses this envelope. Keeps consumers simple."""
    return {
        "eventId": str(uuid.uuid4()),
        "eventType": event_type,
        "eventVersion": "1.0.0",
        "occurredAt": utcnow_iso(),
        "source": source,
        "data": data,
    }


class EventBus:
    """Publish + consume helper built on aio-pika's robust connection."""

    def __init__(self, service_name: str, url: str = RABBITMQ_URL) -> None:
        self.service_name = service_name
        self.url = url
        self.connection: aio_pika.abc.AbstractRobustConnection | None = None
        self.channel: aio_pika.abc.AbstractRobustChannel | None = None
        self.exchange: aio_pika.abc.AbstractExchange | None = None
        self._consumer_tasks: list[asyncio.Task] = []

    # ---------------------------------------------------------------- connect
    async def connect(self, max_attempts: int = 60, delay_seconds: float = 2.0) -> None:
        """Retry until RabbitMQ accepts us. Docker Compose start order is not
        guaranteed, so never assume the broker is up on first try."""
        for attempt in range(1, max_attempts + 1):
            try:
                self.connection = await aio_pika.connect_robust(self.url)
                self.channel = await self.connection.channel()
                await self.channel.set_qos(prefetch_count=10)
                self.exchange = await self.channel.declare_exchange(
                    EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
                )
                logger.info(
                    "[BUS] %s connected to RabbitMQ, topic exchange '%s' ready",
                    self.service_name,
                    EXCHANGE_NAME,
                )
                return
            except Exception as exc:  # noqa: BLE001 - POC: any failure means retry
                logger.warning(
                    "[BUS] %s cannot reach RabbitMQ (attempt %s/%s): %s -- retrying in %ss",
                    self.service_name,
                    attempt,
                    max_attempts,
                    exc,
                    delay_seconds,
                )
                await asyncio.sleep(delay_seconds)
        raise RuntimeError(f"{self.service_name}: gave up connecting to RabbitMQ at {self.url}")

    async def close(self) -> None:
        for task in self._consumer_tasks:
            task.cancel()
        if self.connection is not None:
            await self.connection.close()
        logger.info("[BUS] %s disconnected from RabbitMQ", self.service_name)

    # ---------------------------------------------------------------- publish
    async def publish(self, routing_key: str, event_type: str, data: dict) -> dict:
        if self.exchange is None:
            raise RuntimeError("EventBus.publish called before connect()")
        envelope = build_envelope(event_type, self.service_name, data)
        await self.exchange.publish(
            aio_pika.Message(
                body=json.dumps(envelope).encode("utf-8"),
                content_type="application/json",
                message_id=envelope["eventId"],
                type=event_type,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=routing_key,
        )
        logger.info(
            "[PUBLISH] %s -> exchange=%s routing_key=%s event=%s payload=%s",
            self.service_name,
            EXCHANGE_NAME,
            routing_key,
            event_type,
            json.dumps(envelope["data"]),
        )
        return envelope

    # ---------------------------------------------------------------- consume
    async def consume(
        self,
        queue_name: str,
        routing_keys: Iterable[str],
        handler: Callable[[dict], Awaitable[None]],
    ) -> None:
        """Bind a durable queue to the topic exchange and process messages."""
        if self.channel is None:
            raise RuntimeError("EventBus.consume called before connect()")
        queue = await self.channel.declare_queue(queue_name, durable=True)
        for key in routing_keys:
            await queue.bind(self.exchange, routing_key=key)
            logger.info("[BUS] %s bound queue '%s' to routing key '%s'", self.service_name, queue_name, key)

        async def _on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            async with message.process(requeue=False):
                envelope = json.loads(message.body.decode("utf-8"))
                logger.info(
                    "[CONSUME] %s <- queue=%s routing_key=%s event=%s payload=%s",
                    self.service_name,
                    queue_name,
                    message.routing_key,
                    envelope.get("eventType"),
                    json.dumps(envelope.get("data", {})),
                )
                try:
                    await handler(envelope)
                except Exception:  # noqa: BLE001 - POC: log and move on
                    logger.exception("[CONSUME] %s handler failed for %s", self.service_name, envelope.get("eventType"))

        await queue.consume(_on_message)
        logger.info("[BUS] %s is now consuming from queue '%s'", self.service_name, queue_name)
