"""Links from musical objects back to pixels on the scanned page.

Phase 1 never populates these — the musical layer must work with no scan at all — but the field
exists from day one so Phase 2 does not require a model migration. Nothing downstream may assume
a region is present; call sites use :meth:`SourceRegion.is_reliable` before trusting one.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["BoundingBox", "SourceRegion", "StaffMetrics"]


class BoundingBox(BaseModel):
    """Axis-aligned rectangle in page-image pixel coordinates, origin top-left."""

    model_config = ConfigDict(frozen=True)

    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    def expanded(self, margin: float) -> BoundingBox:
        """Grow by ``margin`` px on every side — used to build crops with context."""
        return BoundingBox(
            x=self.x - margin,
            y=self.y - margin,
            width=self.width + 2 * margin,
            height=self.height + 2 * margin,
        )

    def intersection_over_union(self, other: BoundingBox) -> float:
        """Standard IoU, used to score detector/alignment agreement."""
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return 0.0
        overlap = (right - left) * (bottom - top)
        union = self.width * self.height + other.width * other.height - overlap
        return overlap / union if union else 0.0


class StaffMetrics(BaseModel):
    """Geometry of the staff a symbol belongs to.

    ``staff_space`` (distance between adjacent staff lines) is the natural unit of measurement on
    an engraved page: notehead width, stem length and accidental size are all fixed multiples of
    it, so expressing crop sizes in staff spaces makes the CV stage resolution-independent.
    """

    model_config = ConfigDict(frozen=True)

    top_line_y: float
    staff_space: float = Field(gt=0)
    line_count: int = Field(default=5, ge=1)
    skew_degrees: float = 0.0

    def y_for_position(self, position: int) -> float:
        """Pixel y of a staff position, where 0 is the top line and positive values go down by
        half-spaces (so 1 = first space below the top line)."""
        return self.top_line_y + position * self.staff_space / 2

    def position_for_y(self, y: float) -> float:
        """Inverse of :meth:`y_for_position`; fractional values indicate off-line noteheads."""
        return (y - self.top_line_y) / (self.staff_space / 2)


class SourceRegion(BaseModel):
    """Where a musical object was found on the scan."""

    model_config = ConfigDict(frozen=True)

    page: int = Field(ge=0, description="0-based page index of the source document")
    box: BoundingBox
    staff: StaffMetrics | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    method: str = Field(default="unknown", description="Which alignment stage produced this")

    def is_reliable(self, threshold: float = 0.5) -> bool:
        """Whether this localization is trustworthy enough to crop and classify."""
        return self.confidence >= threshold
