SHELL := /bin/bash
PY    ?= python3
OUT   ?= out/chennai
AOI   ?= mid
DATA  ?= data

.DEFAULT_GOAL := help
.PHONY: help setup deps db data pipeline raster terrain api ui test lint clean clean-data package

help:  ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------- setup

deps:  ## install python dependencies
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e backend[dev]

db:  ## create the PostGIS schema (requires DATABASE_URL or a local cluster)
	@psql "$${DATABASE_URL:-postgresql:///samanvay}" -f backend/samanvay/db/schema.sql

setup: deps  ## install dependencies and prepare directories
	@mkdir -p $(DATA)/raw $(DATA)/aoi out
	@echo "setup complete — run 'make data' next"

# ---------------------------------------------------------------------------- data

data:  ## fetch the real corpus and clip it to the AOI (~2.1 GB download)
	$(PY) data_acquisition/fetch.py --out $(DATA)/raw
	$(PY) data_acquisition/build_aoi.py --raw $(DATA)/raw --out $(DATA)/aoi

# ---------------------------------------------------------------------------- run

pipeline:  ## run the harmonisation DAG over the AOI
	$(PY) scripts/run_pipeline.py --aoi $(AOI) --data $(DATA)/aoi --out $(OUT)

raster:  ## rebuild ORI/DSM from the real UAV corpus and run the null change experiment
	$(PY) scripts/build_raster_tier.py --uav /tmp/uav/data --site aukerman \
	   --zoom 20 --dsm-zoom 18 --out out/raster

terrain:  ## DSM -> DTM -> nDSM -> structures on a calibrated float surface model
	$(PY) scripts/terrain_demo.py --out out/terrain

lod1:  ## build the LOD1 CityJSON city model from the harmonised output
	$(PY) scripts/build_lod1.py --out $(OUT)

evaluate:  ## regenerate the evaluation tables from the last run
	$(PY) scripts/evaluate.py --run $(OUT) --raster out/raster --terrain out/terrain

# ---------------------------------------------------------------------------- serve

api:  ## serve the OGC API and platform API on :8000
	SAMANVAY_OUT=$(OUT) $(PY) -m uvicorn samanvay.api.app:app \
	   --app-dir backend --host 0.0.0.0 --port 8000 --reload

ui: api  ## the console is served by the API at /map

demo:  ## build the single-file offline demo console from the last run
	$(PY) scripts/build_demo.py --out $(OUT) --dest demo/samanvay-demo.html

queue-layer:  ## regenerate the adjudication map layer from a run's checkpoint
	$(PY) scripts/rebuild_queue_layer.py --out $(OUT)

# ---------------------------------------------------------------------------- quality

test:  ## run the test suite
	$(PY) -m pytest tests/ -q

test-real:  ## run the integration tests against the real clipped corpus
	$(PY) -m pytest tests/ -q -m real

lint:  ## static checks
	$(PY) -m ruff check backend/samanvay scripts data_acquisition tests || true
	$(PY) -m mypy backend/samanvay --ignore-missing-imports || true

# ---------------------------------------------------------------------------- misc

clean:  ## remove outputs, keep downloaded data
	rm -rf out .pytest_cache **/__pycache__

clean-data:  ## remove downloaded data as well
	rm -rf $(DATA)/raw $(DATA)/aoi

package:  ## build a source archive of everything except data and outputs
	tar --exclude='data/raw' --exclude='data/aoi' --exclude='out' \
	    --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.ckpt' \
	    -czf samanvay-source.tar.gz .
	@echo "wrote samanvay-source.tar.gz"
