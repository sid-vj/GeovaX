"""Queue-driven pipeline worker.

``docker-compose.yml`` has always declared a ``worker`` service running
``python -m samanvay.pipeline.worker``, but the module didn't exist — the container would
fail to start. This is the real implementation: a Redis-backed job consumer that pops run
requests pushed to the ``samanvay:pipeline:jobs`` list and runs the actual, already-real
``HarmonisationPipeline`` (``pipeline/harmonise.py``) against them, using the same layer
catalogue as the CLI entry point (``pipeline/presets.py``) so the two never drift apart.

Push a job with e.g.::

    redis-cli LPUSH samanvay:pipeline:jobs '{"aoi": "core", "data_dir": "data/aoi", "out_dir": "out/chennai"}'

Run standalone for local testing::

    python -m samanvay.pipeline.worker --once '{"aoi": "test", "data_dir": "data/aoi", "out_dir": "out/tile"}'
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any

from .harmonise import HarmonisationPipeline, HarmoniseConfig
from .presets import AOIS, default_layers

logger = logging.getLogger(__name__)

QUEUE_KEY = "samanvay:pipeline:jobs"


def run_job(job: dict[str, Any]) -> dict[str, Any]:
    """Execute one harmonisation run from a job payload and return its metrics."""
    aoi_key = job.get("aoi", "core")
    if aoi_key not in AOIS:
        raise ValueError(f"Unknown aoi '{aoi_key}'. Known: {sorted(AOIS)}")
    name, bbox = AOIS[aoi_key]
    data_dir = job.get("data_dir", "data/aoi")
    out_dir = job.get("out_dir", "out/chennai")
    max_features = job.get("max_features")

    cfg = HarmoniseConfig(
        aoi_name=name,
        bbox=bbox,
        out_dir=out_dir,
        checkpoint_dir=os.path.join(out_dir, ".ckpt"),
        parcel_pairs=(("TNGIS_CADASTRE", "NCSCM_CADASTRE"),),
        building_pairs=(("GCC_BUILDINGS", "GOOGLE_OPEN_BUILDINGS"),
                         ("GCC_BUILDINGS", "MS_BUILDINGS_TN"),
                         ("GCC_BUILDINGS", "OSM_BUILDINGS_GT")),
    )
    specs = default_layers(data_dir, max_features)
    pipe = HarmonisationPipeline(specs, cfg)

    logger.info("worker: starting run %s (%s) -> %s", pipe.run_id, name, out_dir)
    out = pipe.run()
    logger.info("worker: run %s complete, ledger root %s",
                pipe.run_id, out.metrics.get("ledger", {}).get("merkle_root", "")[:16])
    return out.metrics


def _connect_redis(redis_url: str):
    import redis  # local import: the worker process is the only place this is required
    client = redis.from_url(redis_url, decode_responses=True)
    client.ping()
    return client


def serve_forever(redis_url: str, *, poll_timeout_s: int = 5) -> None:
    """Block on the job queue and process runs one at a time, forever.

    Deliberately single-worker/sequential: the harmonisation pipeline is CPU- and memory-heavy
    per run, and this reference deployment has no job-concurrency requirement. A crashed job
    is logged and the loop continues rather than taking the whole worker down, since a bad AOI
    payload should not block every other queued run.
    """
    client = _connect_redis(redis_url)
    logger.info("worker: connected to Redis at %s, watching '%s'", redis_url, QUEUE_KEY)
    while True:
        item = client.brpop([QUEUE_KEY], timeout=poll_timeout_s)
        if item is None:
            continue
        _, raw = item
        try:
            job = json.loads(raw)
        except json.JSONDecodeError as err:
            logger.error("worker: discarding malformed job payload: %s", err)
            continue
        try:
            run_job(job)
        except Exception:  # noqa: BLE001
            logger.exception("worker: job failed: %s", job)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", metavar="JSON",
                     help="Run a single job payload immediately and exit, instead of serving the queue.")
    ap.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    args = ap.parse_args()

    if args.once:
        job = json.loads(args.once)
        metrics = run_job(job)
        print(json.dumps(metrics, indent=2, default=str))
        return 0

    while True:
        try:
            serve_forever(args.redis_url)
        except Exception as err:  # noqa: BLE001
            logger.error("worker: Redis connection lost (%s); retrying in 5s", err)
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
