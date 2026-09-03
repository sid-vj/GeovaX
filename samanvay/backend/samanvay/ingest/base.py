"""Ingestion framework.

Ingestion in a land-records platform is not "read the file". It is the moment at which the
platform decides what it is looking at, what frame it is in, how much it should be trusted
and what is wrong with it — and records all of that immutably, before a single geometry is
altered. Every later stage depends on getting this right, and every later stage is
recoverable if it is, because the raw claim is still there.

A connector therefore does four things and nothing else:

1. **Probe** — identify format, CRS, geometry types, feature count, encoding, extent.
2. **Profile** — measure the dataset: null rates, attribute cardinality, geometry validity,
   duplicate rate, vertex density, coordinate precision. This is what later lets the
   platform say *why* it trusts one source over another.
3. **Normalise** — reproject to the working CRS, fix encoding, standardise geometry
   dimensionality, without changing meaning.
4. **Emit claims** — every attribute of every feature becomes a ``Claim`` attributed to
   this dataset. Nothing is merged at ingest time.
"""

from __future__ import annotations

import abc
import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

from ..core.models import Claim, FeatureClass, SourceDataset, SourceType


@dataclass
class RawFeature:
    """A feature exactly as the producer wrote it, plus what the probe learned."""

    source_feature_id: str
    geometry: Any                     # shapely geometry in the dataset's own CRS
    properties: dict[str, Any] = field(default_factory=dict)
    crs: str = "EPSG:4326"
    dataset_id: str = ""
    warnings: list[str] = field(default_factory=list)

    def claims(self, source_type: SourceType, observed_on: datetime | None = None,
               accuracy_m: float | None = None) -> list[Claim]:
        out = [
            Claim(
                dataset_id=self.dataset_id,
                source_type=source_type,
                property_path="geometry",
                value=self.geometry.wkt if self.geometry is not None else None,
                observed_on=observed_on,
                accuracy_m=accuracy_m,
                source_feature_id=self.source_feature_id,
            )
        ]
        for key, value in self.properties.items():
            if value is None or value == "":
                continue
            out.append(
                Claim(
                    dataset_id=self.dataset_id,
                    source_type=source_type,
                    property_path=key,
                    value=value,
                    observed_on=observed_on,
                    source_feature_id=self.source_feature_id,
                )
            )
        return out


@dataclass
class DatasetProfile:
    """Everything measurable about a dataset, computed once at ingest."""

    dataset_id: str
    feature_count: int = 0
    geometry_types: dict[str, int] = field(default_factory=dict)
    crs_declared: str | None = None
    crs_inferred: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    attribute_null_rate: dict[str, float] = field(default_factory=dict)
    attribute_cardinality: dict[str, int] = field(default_factory=dict)
    attribute_types: dict[str, str] = field(default_factory=dict)
    invalid_geometry_count: int = 0
    empty_geometry_count: int = 0
    duplicate_geometry_count: int = 0
    mean_vertex_count: float = 0.0
    coordinate_precision_digits: int = 0
    """Decimal digits actually carried. 4 digits at Chennai's latitude is 11 m — a
    dataset stored at 4 digits cannot support a cadastral claim regardless of its
    nominal accuracy."""
    warnings: list[str] = field(default_factory=list)

    def implied_precision_m(self, latitude: float = 13.0) -> float:
        import math
        return 10 ** (-self.coordinate_precision_digits) * 111_320 * math.cos(math.radians(latitude))

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["implied_precision_m"] = round(self.implied_precision_m(), 4)
        return d


class Connector(abc.ABC):
    """Base class for every input format."""

    #: file extensions this connector claims
    extensions: tuple[str, ...] = ()

    def __init__(self, dataset: SourceDataset, feature_class: FeatureClass) -> None:
        self.dataset = dataset
        self.feature_class = feature_class
        self.profile = DatasetProfile(dataset_id=dataset.dataset_id)

    @classmethod
    def handles(cls, path: str) -> bool:
        return path.lower().endswith(cls.extensions)

    @abc.abstractmethod
    def probe(self, path: str) -> DatasetProfile:
        """Cheap inspection: never loads the whole dataset."""

    @abc.abstractmethod
    def read(self, path: str, **kwargs: Any) -> Iterator[RawFeature]:
        """Stream features. Must not hold the whole dataset in memory."""

    # -- shared helpers -----------------------------------------------------------

    @staticmethod
    def checksum(path: str, chunk: int = 1 << 20) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                b = fh.read(chunk)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()

    @staticmethod
    def file_mtime(path: str) -> datetime:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)

    @staticmethod
    def coordinate_precision(values: Iterable[float], cap: int = 12) -> int:
        """Largest number of decimals actually used across a coordinate sample."""
        best = 0
        for v in values:
            s = f"{v:.{cap}f}".rstrip("0")
            if "." in s:
                best = max(best, len(s.split(".")[1]))
        return best


class ConnectorRegistry:
    """Dispatches a path to the connector that claims it."""

    def __init__(self) -> None:
        self._connectors: list[type[Connector]] = []

    def register(self, cls: type[Connector]) -> type[Connector]:
        self._connectors.append(cls)
        return cls

    def for_path(self, path: str) -> type[Connector]:
        for c in self._connectors:
            if c.handles(path):
                return c
        raise ValueError(
            f"no connector for {path!r}; registered extensions: "
            + ", ".join(sorted({e for c in self._connectors for e in c.extensions}))
        )


REGISTRY = ConnectorRegistry()
