"""Industrial Enterprise Service Integrations:
1. Redis: In-memory distributed caching for high-throughput spatial query performance.
2. Apache Kafka: Real-time event streaming bus for cross-departmental synchronization.
3. OpenSearch / Elasticsearch: High-speed full-text & geospatial indexing for land records.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ==============================================================================
# 1. Redis Caching Service
# ==============================================================================

class RedisCacheService:
    """Redis distributed cache client with graceful local memory fallback."""
    
    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._client = None
        self._memory_cache: dict[str, Any] = {}
        self._init_client()

    def _init_client(self) -> None:
        try:
            import redis
            self._client = redis.from_url(self.redis_url, decode_responses=True, socket_timeout=1.0)
            self._client.ping()
            logger.info("Connected to Redis cache at %s", self.redis_url)
        except Exception as err:
            logger.info("Redis not available (%s); operating with in-memory cache fallback.", err)
            self._client = None

    def get(self, key: str) -> Optional[Any]:
        if self._client:
            try:
                val = self._client.get(key)
                return json.loads(val) if val else None
            except Exception:
                pass
        return self._memory_cache.get(key)

    def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        if self._client:
            try:
                self._client.setex(key, ttl_seconds, json.dumps(value, default=str))
                return
            except Exception:
                pass
        self._memory_cache[key] = value

    def invalidate(self, pattern: str = "*") -> None:
        if self._client:
            try:
                keys = self._client.keys(pattern)
                if keys:
                    self._client.delete(*keys)
            except Exception:
                pass
        self._memory_cache.clear()


# ==============================================================================
# 2. Apache Kafka Event Producer
# ==============================================================================

class KafkaEventBus:
    """Apache Kafka event producer for real-time land record state notifications."""
    
    TOPIC_ADJUDICATION = "geovax.events.adjudication"
    TOPIC_MUTATION = "geovax.events.parcel.mutated"
    TOPIC_LITIGATION = "geovax.events.litigation.flagged"

    def __init__(self, bootstrap_servers: Optional[str] = None):
        self.bootstrap_servers = bootstrap_servers or os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        self._producer = None
        self._event_log: list[dict[str, Any]] = []
        self._init_producer()

    def _init_producer(self) -> None:
        try:
            from kafka import KafkaProducer
            self._producer = KafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
                request_timeout_ms=1000,
            )
            logger.info("Connected to Apache Kafka broker at %s", self.bootstrap_servers)
        except Exception as err:
            logger.info("Kafka broker not reachable (%s); queueing events in local audit ledger.", err)
            self._producer = None

    def emit(self, topic: str, key: str, payload: dict[str, Any]) -> bool:
        """Emit real-time event to Kafka topic or local ledger. Returns True only if this
        actually reached a real Kafka broker — callers (e.g. /api/adjudication/resolve) use
        this to report an honest `kafka_event_emitted` rather than always claiming success."""
        event_envelope = {
            "topic": topic,
            "key": key,
            "timestamp": payload.get("timestamp"),
            "payload": payload,
        }
        if self._producer:
            try:
                self._producer.send(topic, key=key.encode("utf-8"), value=payload)
                self._producer.flush()
                return True
            except Exception as err:
                logger.warning("Failed emitting to Kafka: %s", err)

        self._event_log.append(event_envelope)
        logger.info("[KafkaBus Local] Topic: %s | Key: %s | Payload keys: %s", topic, key, list(payload.keys()))
        return False

    def get_recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._event_log[-limit:]


# ==============================================================================
# 3. OpenSearch / Elasticsearch Search Service
# ==============================================================================

class OpenSearchService:
    """OpenSearch client for full-text, survey number, and geo-distance queries."""
    
    INDEX_PARCELS = "samanvay_parcels"
    INDEX_BUILDINGS = "samanvay_buildings"

    def __init__(self, endpoint: Optional[str] = None):
        self.endpoint = endpoint or os.getenv("OPENSEARCH_URL", "http://localhost:9200")
        self._client = None
        self._indexed_docs: list[dict[str, Any]] = []
        self._init_client()

    def _init_client(self) -> None:
        try:
            from opensearchpy import OpenSearch
            self._client = OpenSearch([self.endpoint], verify_certs=False, timeout=1.0)
            if self._client.ping():
                logger.info("Connected to OpenSearch at %s", self.endpoint)
            else:
                self._client = None
        except Exception:
            self._client = None

    def index_feature(self, index: str, doc_id: str, body: dict[str, Any]) -> None:
        if self._client:
            try:
                self._client.index(index=index, id=doc_id, body=body)
                return
            except Exception:
                pass
        self._indexed_docs.append({"index": index, "id": doc_id, "body": body})

    def search(self, query_text: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search records by survey number, ULPIN, village, or ward."""
        if self._client:
            try:
                res = self._client.search(
                    index=self.INDEX_PARCELS,
                    body={
                        "size": limit,
                        "query": {
                            "multi_match": {
                                "query": query_text,
                                "fields": ["ulpin^3", "survey_number^2", "village", "ward", "subdivision"],
                                "fuzziness": "AUTO",
                            }
                        },
                    },
                )
                return [h["_source"] for h in res.get("hits", {}).get("hits", [])]
            except Exception:
                pass

        # In-memory search fallback
        q = query_text.lower().strip()
        matches = []
        for doc in self._indexed_docs:
            b = doc["body"]
            match_str = f"{b.get('ulpin', '')} {b.get('survey_number', '')} {b.get('village', '')} {b.get('ward', '')}".lower()
            if q in match_str:
                matches.append(b)
                if len(matches) >= limit:
                    break
        return matches


# Global Singletons
cache_service = RedisCacheService()
kafka_bus = KafkaEventBus()
opensearch_service = OpenSearchService()
