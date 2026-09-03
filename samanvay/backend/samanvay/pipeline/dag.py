"""A small dependency-ordered task runner.

Airflow, Prefect and Dagster all solve this problem well and none of them should be a hard
dependency of a system that has to run in a district office on a machine with no internet
access. The requirements here are modest and specific:

* declare stages and their dependencies,
* run them in topological order, optionally in parallel where the graph allows,
* checkpoint each stage's output so a failed run resumes rather than restarts — which
  matters enormously when stage 3 of 9 takes forty minutes,
* record timing, memory and a per-stage report for the run record,
* fail loudly and leave the partial state inspectable.

That is about two hundred lines, and it removes an entire class of deployment problem.
"""

from __future__ import annotations

import json
import os
import pickle
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


@dataclass
class StageResult:
    name: str
    status: str = "pending"     # pending | running | ok | failed | skipped | cached
    seconds: float = 0.0
    report: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    traceback: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "status": self.status,
            "seconds": round(self.seconds, 3),
            "report": self.report,
            "error": self.error,
        }


@dataclass
class Stage:
    name: str
    fn: Callable[[dict[str, Any]], Any]
    depends_on: tuple[str, ...] = ()
    description: str = ""
    cacheable: bool = True
    optional: bool = False
    """An optional stage that fails does not fail the run — used for stages that depend on
    data a given deployment may simply not have, such as GNSS control."""


class Dag:
    def __init__(self, name: str, *, checkpoint_dir: str | None = None,
                 max_workers: int = 1) -> None:
        self.name = name
        self.stages: dict[str, Stage] = {}
        self.results: dict[str, StageResult] = {}
        self.context: dict[str, Any] = {}
        self.checkpoint_dir = checkpoint_dir
        self.max_workers = max_workers
        self._listeners: list[Callable[[StageResult], None]] = []

    # -- construction --------------------------------------------------------------

    def stage(self, name: str, depends_on: Iterable[str] = (), *,
              description: str = "", cacheable: bool = True, optional: bool = False):
        def deco(fn: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
            self.add(Stage(name, fn, tuple(depends_on), description, cacheable, optional))
            return fn
        return deco

    def add(self, stage: Stage) -> None:
        if stage.name in self.stages:
            raise ValueError(f"duplicate stage {stage.name!r}")
        self.stages[stage.name] = stage
        self.results[stage.name] = StageResult(stage.name)

    def on_stage(self, fn: Callable[[StageResult], None]) -> None:
        self._listeners.append(fn)

    # -- ordering ------------------------------------------------------------------

    def topological_layers(self) -> list[list[str]]:
        """Group stages into layers that can each run in parallel."""
        remaining = {n: set(s.depends_on) for n, s in self.stages.items()}
        for n, deps in remaining.items():
            missing = deps - set(self.stages)
            if missing:
                raise ValueError(f"stage {n!r} depends on unknown stage(s) {sorted(missing)}")
        layers: list[list[str]] = []
        done: set[str] = set()
        while remaining:
            ready = sorted(n for n, deps in remaining.items() if deps <= done)
            if not ready:
                raise ValueError(
                    f"cycle detected among stages {sorted(remaining)}; the dependency "
                    f"graph must be acyclic"
                )
            layers.append(ready)
            done |= set(ready)
            for n in ready:
                remaining.pop(n)
        return layers

    # -- execution -----------------------------------------------------------------

    def run(self, context: dict[str, Any] | None = None, *,
            only: Iterable[str] | None = None,
            resume: bool = True) -> dict[str, StageResult]:
        self.context = dict(context or {})
        selected = set(only) if only else None
        for layer in self.topological_layers():
            todo = [n for n in layer if selected is None or n in selected]
            if not todo:
                continue
            if self.max_workers > 1 and len(todo) > 1:
                with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                    list(ex.map(lambda n: self._run_stage(n, resume), todo))
            else:
                for n in todo:
                    self._run_stage(n, resume)
            failed = [n for n in todo
                      if self.results[n].status == "failed" and not self.stages[n].optional]
            if failed:
                raise RuntimeError(
                    f"stage(s) {failed} failed; the run is stopped so the partial state "
                    f"can be inspected. See results[...].traceback."
                )
        return self.results

    def _run_stage(self, name: str, resume: bool) -> None:
        stage = self.stages[name]
        result = self.results[name]

        cached = self._load_checkpoint(name) if (resume and stage.cacheable) else None
        if cached is not None:
            self.context[name] = cached["value"]
            result.status = "cached"
            result.report = cached.get("report", {})
            result.seconds = cached.get("seconds", 0.0)
            self._notify(result)
            return

        result.status = "running"
        self._notify(result)
        t0 = time.time()
        try:
            value = stage.fn(self.context)
            self.context[name] = value
            result.status = "ok"
            if isinstance(value, dict) and "_report" in value:
                result.report = value["_report"]
            elif hasattr(value, "summary") and callable(value.summary):
                result.report = {"summary": value.summary()}
        except Exception as exc:  # noqa: BLE001
            result.status = "failed"
            result.error = f"{type(exc).__name__}: {exc}"
            result.traceback = traceback.format_exc()
        finally:
            result.seconds = time.time() - t0
        if result.status == "ok" and stage.cacheable:
            self._save_checkpoint(name, self.context.get(name), result)
        self._notify(result)

    def _notify(self, r: StageResult) -> None:
        for fn in self._listeners:
            try:
                fn(r)
            except Exception:  # noqa: BLE001
                pass

    # -- checkpoints ---------------------------------------------------------------

    def _path(self, name: str) -> str | None:
        if not self.checkpoint_dir:
            return None
        return os.path.join(self.checkpoint_dir, f"{self.name}.{name}.pkl")

    def _save_checkpoint(self, name: str, value: Any, result: StageResult) -> None:
        p = self._path(name)
        if not p:
            return
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                pickle.dump({"value": value, "report": result.report,
                             "seconds": result.seconds}, fh)
        except Exception:  # noqa: BLE001
            pass  # a checkpoint that cannot be written must never fail the run

    def _load_checkpoint(self, name: str) -> dict[str, Any] | None:
        p = self._path(name)
        if not p or not os.path.exists(p):
            return None
        try:
            with open(p, "rb") as fh:
                return pickle.load(fh)
        except Exception:  # noqa: BLE001
            return None

    def clear_checkpoints(self) -> int:
        if not self.checkpoint_dir or not os.path.isdir(self.checkpoint_dir):
            return 0
        n = 0
        for fn in os.listdir(self.checkpoint_dir):
            if fn.startswith(self.name + "."):
                os.remove(os.path.join(self.checkpoint_dir, fn))
                n += 1
        return n

    # -- reporting -----------------------------------------------------------------

    def report(self) -> dict[str, Any]:
        return {
            "dag": self.name,
            "stages": [self.results[n].to_dict() for n in self.stages],
            "total_seconds": round(sum(r.seconds for r in self.results.values()), 2),
            "status": ("failed" if any(r.status == "failed" for r in self.results.values())
                       else "ok"),
        }

    def to_mermaid(self) -> str:
        lines = ["graph LR"]
        for n, s in self.stages.items():
            label = n.replace("_", " ")
            lines.append(f'    {n}["{label}"]')
        for n, s in self.stages.items():
            for d in s.depends_on:
                lines.append(f"    {d} --> {n}")
        return "\n".join(lines)

    def save_report(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.report(), fh, indent=2, default=str)
