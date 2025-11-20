import os
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import requests

app = FastAPI(title="Laundromat Finder API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OSM_USER_AGENT = os.getenv("OSM_USER_AGENT", "LaundromatFinder/1.0 (+https://example.com)")


@app.get("/")
def read_root():
    return {"message": "Laundromat Finder Backend Running"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/test")
def test_database():
    """Basic healthcheck for backend. Database optional for this app."""
    response = {
        "backend": "✅ Running",
        "database": "ℹ️ Not required for search",
        "database_url": None,
        "database_name": None,
        "connection_status": "N/A",
        "collections": []
    }
    # Check environment variables that would be used if DB needed
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


def geocode_location(query: str) -> Optional[Dict[str, Any]]:
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 1}
    headers = {"User-Agent": OSM_USER_AGENT}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    if r.status_code != 200:
        return None
    data = r.json()
    if not data:
        return None
    item = data[0]
    return {
        "lat": float(item.get("lat")),
        "lon": float(item.get("lon")),
        "display_name": item.get("display_name"),
    }


def build_address(tags: Dict[str, Any]) -> Optional[str]:
    parts = []
    num = tags.get("addr:housenumber")
    street = tags.get("addr:street")
    if street:
        parts.append(f"{num + ' ' if num else ''}{street}")
    city = tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village")
    state = tags.get("addr:state") or tags.get("addr:province")
    postcode = tags.get("addr:postcode")
    locality = ", ".join([p for p in [city, state] if p])
    if locality:
        parts.append(locality)
    if postcode:
        parts.append(postcode)
    return ", ".join(parts) if parts else None


def meters_to_km(m: Optional[float]) -> Optional[float]:
    if m is None:
        return None
    return round(m / 1000.0, 2)


@app.get("/api/search")
def search_laundromats(
    query: str = Query(..., description="City, address, or place name to search around"),
    radius_km: float = Query(5.0, ge=0.5, le=50.0, description="Search radius in kilometers"),
    max_results: int = Query(50, ge=1, le=100, description="Max number of places to return")
):
    """Search laundromats near a given location using OpenStreetMap (Overpass)."""
    # Step 1: Geocode the query to lat/lon
    center = geocode_location(query)
    if not center:
        raise HTTPException(status_code=404, detail="Location not found. Try a different search.")

    lat = center["lat"]
    lon = center["lon"]
    radius_m = int(radius_km * 1000)

    # Step 2: Query Overpass for laundromats around the point
    overpass_url = "https://overpass-api.de/api/interpreter"
    # Look for both nodes and ways with relevant tags
    query_template = f"""
    [out:json][timeout:25];
    (
      node["shop"="laundry"](around:{radius_m},{lat},{lon});
      way["shop"="laundry"](around:{radius_m},{lat},{lon});
      node["amenity"="laundry"](around:{radius_m},{lat},{lon});
      way["amenity"="laundry"](around:{radius_m},{lat},{lon});
      node["amenity"="laundrette"](around:{radius_m},{lat},{lon});
      way["amenity"="laundrette"](around:{radius_m},{lat},{lon});
    );
    out center tags qt {max_results};
    """
    headers = {"User-Agent": OSM_USER_AGENT, "Content-Type": "text/plain"}
    r = requests.post(overpass_url, data=query_template.strip(), headers=headers, timeout=45)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Upstream map service error. Please try again later.")

    data = r.json()
    elements = data.get("elements", [])

    results: List[Dict[str, Any]] = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name") or "Laundromat"
        # Determine coordinates (node vs way with center)
        if el.get("type") == "node":
            la = el.get("lat")
            lo = el.get("lon")
        else:
            center_obj = el.get("center") or {}
            la = center_obj.get("lat")
            lo = center_obj.get("lon")
        if la is None or lo is None:
            continue

        # Try distance if Overpass provides it (not always). We'll skip computing haversine to keep it simple.
        distance_km = None
        addr = build_address(tags) or tags.get("addr:full")

        results.append({
            "id": f"{el.get('type')}/{el.get('id')}",
            "name": name,
            "address": addr,
            "lat": la,
            "lon": lo,
            "distance_km": distance_km,
            "opening_hours": tags.get("opening_hours"),
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "website": tags.get("website") or tags.get("contact:website"),
            "osm_url": f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}"
        })

    # Sort with name and limit
    results = sorted(results, key=lambda x: (x["name"] or "zzzz"))[:max_results]

    return {
        "center": center,
        "count": len(results),
        "results": results,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
