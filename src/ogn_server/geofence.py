import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


def load_geofences(filepath: str) -> Dict[str, List[Dict[str, Any]]]:
    """Load geofences from a JSON file.

    The function tolerates a few common shapes, returning a normalized
    structure: { "geofences": [ { "name": ..., "polygon": [ [lat, lon], ... ] }, ... ] }

    - Accepts either a top-level list or dictionary with keys like
      "geofences", "airports", or "polygons". Each item may be a dict
      describing a geofence or a raw list of coordinate pairs.
    - Logs structured information for easier tracing.
    - Raises FileNotFoundError or json.JSONDecodeError on failure.
    """
    path = Path(filepath)
    try:
        with path.open("r", encoding="utf-8") as f:
            data: Any = json.load(f)
    except FileNotFoundError:
        logger.info({"event": "load_geofences", "status": "file_not_found", "path": str(filepath), "message": "Geofencing disabled - create geofences.json to enable"})
        raise
    except json.JSONDecodeError as e:
        logger.error({"event": "load_geofences", "error": "invalid_json", "path": str(filepath), "detail": str(e)})
        raise

    # Normalize to a list of geofence items, each with a polygon.
    raw_list: List[Any] = []
    if isinstance(data, list):
        raw_list = data
    elif isinstance(data, dict):
        if isinstance(data.get("geofences"), list):
            raw_list = data["geofences"]
        elif isinstance(data.get("airports"), list):
            raw_list = data["airports"]
        elif isinstance(data.get("polygons"), list):
            raw_list = data["polygons"]
        else:
            # Fallback: grab any top-level list-like value
            for v in data.values():
                if isinstance(v, list):
                    raw_list = v
                    break
    else:
        raw_list = []

    geofences: List[Dict[str, Any]] = []
    for item in raw_list:
        polygon: List[Tuple[float, float]] | None = None
        name: str | None = None

        if isinstance(item, dict):
            name = item.get("name") if isinstance(item.get("name"), str) else None
            if isinstance(item.get("polygon"), list):
                polygon = [(float(pt[0]), float(pt[1])) for pt in item["polygon"] if isinstance(pt, (list, tuple)) and len(pt) >= 2]
            elif isinstance(item.get("points"), list):
                polygon = [(float(pt[0]), float(pt[1])) for pt in item["points"] if isinstance(pt, (list, tuple)) and len(pt) >= 2]
        elif isinstance(item, (list, tuple)):
            # Assume raw polygon coordinates: [ [lat, lon], ... ]
            coords = item
            polygon = [(float(pt[0]), float(pt[1])) for pt in coords if isinstance(pt, (list, tuple)) and len(pt) >= 2]

        if polygon:
            geofences.append({"name": name, "polygon": polygon})

    logger.info({"event": "load_geofences", "path": str(filepath), "count": len(geofences)})
    return {"geofences": geofences}


def point_in_polygon(lat: float, lon: float, polygon: List[Tuple[float, float]]) -> bool:
    """Ray casting algorithm to determine if a point is inside a polygon.

    The polygon is a list of (lat, lon) pairs. We treat lon as x and lat as y
    for computation consistency with typical geographic coordinates.
    """
    if not polygon or len(polygon) < 3:
        return False

    y = float(lat)
    x = float(lon)
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        yi, xi = float(polygon[i][0]), float(polygon[i][1])
        yj, xj = float(polygon[j][0]), float(polygon[j][1])
        # Check if edge crosses horizontal ray at y
        if ((yi > y) != (yj > y)):
            # Compute x-coordinate of intersection of the edge with the horizontal line at y
            x_intersect = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_intersect:
                inside = not inside
        j = i
    return inside


def is_off_field(lat: float, lon: float, geofences: Dict[str, Any]) -> bool:
    """Return True if the given position is outside ALL defined geofences.

    - If there are no geofences defined, returns True (off-field).
    - If the position is inside any geofence polygon, returns False (on-field).
    - Otherwise, returns True (off-field).
    """
    if not geofences:
        return True

    polygons: List[List[Tuple[float, float]]] = []

    items: List[Any] = []
    if isinstance(geofences, dict) and isinstance(geofences.get("geofences"), list):
        items = geofences["geofences"]
    elif isinstance(geofences, dict) and isinstance(geofences.get("polygons"), list):
        items = geofences["polygons"]
    elif isinstance(geofences, list):
        items = geofences
    else:
        items = []

    for item in items:
        poly_coords: List[Tuple[float, float]] | None = None
        if isinstance(item, dict):
            if isinstance(item.get("polygon"), list):
                poly_coords = [(float(pt[0]), float(pt[1])) for pt in item["polygon"] if isinstance(pt, (list, tuple)) and len(pt) >= 2]
            elif isinstance(item.get("points"), list):
                poly_coords = [(float(pt[0]), float(pt[1])) for pt in item["points"] if isinstance(pt, (list, tuple)) and len(pt) >= 2]
        elif isinstance(item, (list, tuple)):
            coords = item
            poly_coords = [(float(pt[0]), float(pt[1])) for pt in coords if isinstance(pt, (list, tuple)) and len(pt) >= 2]

        if poly_coords:
            polygons.append(poly_coords)

    if not polygons:
        return True

    # If the point is inside any polygon, it's not off-field
    for poly in polygons:
        if point_in_polygon(float(lat), float(lon), poly):
            return False
    return True
