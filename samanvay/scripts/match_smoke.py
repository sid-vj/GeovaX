"""Smoke test: match the real GCC municipal footprints against the real Google Open
Buildings extraction over one tile of central Chennai."""
import sys, json, time
from collections import Counter

sys.path.insert(0, "/home/claude/samanvay/backend")
from shapely.geometry import shape
from shapely.ops import transform as T
from pyproj import Transformer

from samanvay.matching.features import MatchableFeature, BlockingConfig
from samanvay.matching.pipeline import MatchingPipeline

TILE = (80.235, 13.070, 80.250, 13.085)
tr = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)


def load(path, ds):
    out = []
    with open(path) as f:
        for i, l in enumerate(f):
            o = json.loads(l)
            g = o.get("geometry")
            if not g:
                continue
            s = shape(g)
            b = s.bounds
            if b[2] < TILE[0] or b[0] > TILE[2] or b[3] < TILE[1] or b[1] > TILE[3]:
                continue
            sm = T(lambda x, y, z=None: tr.transform(x, y), s)
            p = o.get("properties") or {}
            fid = str(p.get("gcc_gis_id") or p.get("gob_id") or f"{ds}#{i}")
            out.append(MatchableFeature(fid=fid, dataset_id=ds, geometry=sm, attributes=p))
    return out


t0 = time.time()
A = load("/home/claude/samanvay/data/aoi/buildings_gcc.geojsonl", "gcc")
print("A", len(A), round(time.time() - t0, 1), flush=True)
B = load("/home/claude/samanvay/data/aoi/buildings_gob.geojsonl", "gob")
print("B", len(B), round(time.time() - t0, 1), flush=True)

pipe = MatchingPipeline(blocking=BlockingConfig(accuracy_multiplier=3.0,
                                               max_candidates_per_feature=8))
res = pipe.run(A, B, acc_left_m=1.0, acc_right_m=1.8)
print(res.summary(), flush=True)
print("events", Counter(e.kind for e in res.events), flush=True)
if res.training:
    print("importance", res.training.feature_importance, flush=True)
    print("lf coverage", res.training.lf_coverage, flush=True)
