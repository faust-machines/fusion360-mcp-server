"""Pure geometry helpers for ``create_hole``.

Deliberately free of ``adsk`` imports so the face-picking and plane-solving
rules can be unit-tested without a running Fusion instance.
"""

from __future__ import annotations

# Minimum |normal.z| for a planar face to count as horizontal, i.e. to be a
# candidate "top"/"bottom" face. 0.99995 allows roughly 0.6 degrees of tilt.
HORIZONTAL_FACE_MIN_NZ = 0.99995


def is_horizontal_face(
    normal_z: float, want_up: bool, min_nz: float = HORIZONTAL_FACE_MIN_NZ
) -> bool:
    """True if a face with this outward normal faces up (or down) and is flat.

    Comparing bounding boxes instead would also accept the *side* faces of a
    box, whose bounding boxes reach the body's top too.
    """
    return (normal_z if want_up else -normal_z) >= min_nz


def face_rank_key(edge_z: float, area: float, want_up: bool) -> tuple[float, float]:
    """Sort key picking the highest (or lowest) face, ties going to the larger.

    *edge_z* is the face's own extreme Z (top of its bounding box when looking
    for a top face). Within the tilt tolerance that is not the same as the Z of
    an arbitrary point on the face.
    """
    return (edge_z if want_up else -edge_z, area)


def plane_z_at(
    normal: tuple[float, float, float],
    origin: tuple[float, float, float],
    x: float,
    y: float,
) -> float:
    """Z where the vertical line through (*x*, *y*) meets the plane.

    Any point on the face has the right Z only if the face is exactly
    horizontal, so solve the plane instead of reusing ``face.pointOnFace``.
    """
    nx, ny, nz = normal
    ox, oy, oz = origin
    if nz == 0:
        raise ValueError("plane is vertical: no unique Z above (x, y)")
    return oz - (nx * (x - ox) + ny * (y - oy)) / nz
