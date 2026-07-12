import { useState, useCallback, useEffect, useRef } from "react";
import ReactMapGL, { Source, Layer, Marker, NavigationControl, type MapLayerMouseEvent } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import { planTrip, fetchDirectBuses, type TripPlanResult, type DirectBusResult } from "../api";

const BENGALURU_CENTER = { latitude: 12.9716, longitude: 77.5946, zoom: 12 };

const MAP_STYLE = {
  version: 8 as const,
  sources: {
    "osm-tiles": {
      type: "raster" as const,
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm-tiles-layer", type: "raster" as const, source: "osm-tiles" }],
};

type TimeSlot = "MORNING" | "AFTERNOON" | "EVENING" | "NIGHT";
type Point = { lat: number; lon: number; label: string };

const MODE_COLORS: Record<string, string> = { WALK: "#6B7280", BUS: "#2563EB", METRO: "#16A34A" };
const MODE_ICONS: Record<string, string> = { WALK: "🚶", BUS: "🚌", METRO: "🚇" };

interface NominatimResult {
  display_name: string;
  lat: string;
  lon: string;
}

const BENGALURU_VIEWBOX = "77.35,13.15,77.85,12.75";

async function searchPlaces(query: string): Promise<NominatimResult[]> {
  if (query.trim().length < 3) return [];
  const url = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(
    query
  )}&viewbox=${BENGALURU_VIEWBOX}&bounded=0&limit=5`;
  const res = await fetch(url);
  if (!res.ok) return [];
  return res.json();
}

function LocationSearchBox({
  placeholder,
  onSelect,
  value,
}: {
  placeholder: string;
  onSelect: (point: Point) => void;
  value: string;
}) {
  const [query, setQuery] = useState(value);
  const [suggestions, setSuggestions] = useState<NominatimResult[]>([]);
  const [open, setOpen] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => setQuery(value), [value]);

  function handleChange(v: string) {
    setQuery(v);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      const results = await searchPlaces(v);
      setSuggestions(results);
      setOpen(results.length > 0);
    }, 450);
  }

  return (
    <div style={{ position: "relative" }}>
      <input
        value={query}
        onChange={(e) => handleChange(e.target.value)}
        placeholder={placeholder}
        style={searchInputStyle}
        onFocus={() => suggestions.length > 0 && setOpen(true)}
      />
      {open && (
        <div style={suggestionBoxStyle}>
          {suggestions.map((s, idx) => (
            <div
              key={idx}
              style={suggestionItemStyle}
              onClick={() => {
                onSelect({ lat: parseFloat(s.lat), lon: parseFloat(s.lon), label: s.display_name });
                setQuery(s.display_name);
                setOpen(false);
              }}
            >
              {s.display_name}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function TripPlanner() {
  const [sourcePoint, setSourcePoint] = useState<Point | null>(null);
  const [destPoint, setDestPoint] = useState<Point | null>(null);
  const [sourceMode, setSourceMode] = useState<"live" | "search">("live");
  const [timeSlot, setTimeSlot] = useState<TimeSlot>("MORNING");
  const [result, setResult] = useState<TripPlanResult | null>(null);
  const [directBuses, setDirectBuses] = useState<DirectBusResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"overview" | "bus" | "cab">("overview");
  const lastPlannedSourceRef = useRef<Point | null>(null);

  function distanceMeters(a: Point, b: Point): number {
    const R = 6371000;
    const dLat = ((b.lat - a.lat) * Math.PI) / 180;
    const dLon = ((b.lon - a.lon) * Math.PI) / 180;
    const s =
      Math.sin(dLat / 2) ** 2 +
      Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
    return R * 2 * Math.asin(Math.sqrt(s));
  }

  useEffect(() => {
    if (!navigator.geolocation) return;

    // watchPosition = continuous tracking (Google Maps ke "blue dot" jaisa) —
    // sirf ek baar location lene ke bajaye, position badalte hi update hoti rahegi
    const watchId = navigator.geolocation.watchPosition(
      (pos) => {
        setSourcePoint((prev) => {
          // Agar user manually search se koi doosri location choose kar chuka hai,
          // to live tracking usko overwrite nahi karegi (sirf "live" mode mein update hoga)
          if (sourceMode !== "live") return prev;
          return { lat: pos.coords.latitude, lon: pos.coords.longitude, label: "Live Location" };
        });
      },
      () => {},
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 5000 }
    );

    return () => navigator.geolocation.clearWatch(watchId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceMode]);

  useEffect(() => {
    if (sourcePoint && destPoint) {
      const last = lastPlannedSourceRef.current;
      if (last && distanceMeters(last, sourcePoint) < 40) {
        return; // bahut chota movement hai (GPS jitter) — dobara plan mat karo
      }
      lastPlannedSourceRef.current = sourcePoint;
      runPlanTrip(sourcePoint, destPoint, timeSlot);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourcePoint, destPoint]);

  useEffect(() => {
    if (sourcePoint && destPoint) {
      runPlanTrip(sourcePoint, destPoint, timeSlot);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeSlot]);

  async function runPlanTrip(src: Point, dst: Point, slot: TimeSlot) {
    setLoading(true);
    setError(null);
    try {
      const data = await planTrip(src.lat, src.lon, dst.lat, dst.lon, slot);
      setResult(data);
      setActiveTab("overview");
    } catch (err) {
      setError("Trip plan nahi ban paya — is area mein transit data nahi mila.");
      console.error(err);
    } finally {
      setLoading(false);
    }

    try {
      const now = new Date();
      const currentTimeStr = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:00`;
      const buses = await fetchDirectBuses(src.lat, src.lon, dst.lat, dst.lon, currentTimeStr);
      setDirectBuses(buses);
    } catch (err) {
      setDirectBuses(null);
      console.error(err);
    }
  }

  const handleMapClick = useCallback((e: MapLayerMouseEvent) => {
    const { lat, lng } = e.lngLat;
    setDestPoint({ lat, lon: lng, label: `${lat.toFixed(4)}, ${lng.toFixed(4)}` });
  }, []);

  function resetTrip() {
    setDestPoint(null);
    setResult(null);
    setDirectBuses(null);
    setError(null);
  }

  function useLiveLocation() {
    setSourceMode("live");
    navigator.geolocation.getCurrentPosition(
      (pos) => setSourcePoint({ lat: pos.coords.latitude, lon: pos.coords.longitude, label: "Live Location" }),
      () => setError("Live location nahi mila — permission check karo.")
    );
  }

  const stepFeatures = result
    ? result.transit_option.steps.map((step, idx) => ({
        type: "Feature" as const,
        properties: { mode: step.mode, idx },
        geometry: { type: "LineString" as const, coordinates: step.coordinates },
      }))
    : [];
  const stepsGeoJson = { type: "FeatureCollection" as const, features: stepFeatures };

  // Note: ab real road-shape seedha BUS steps ki coordinates mein hi aata hai
  // (backend se), isliye alag se ek "generic" shape line dikhane ki zarurat nahi —
  // isse confusion nahi hoga ki kaunsa line kis cheez ko represent karta hai.

  const uberLink =
    sourcePoint && destPoint
      ? `https://m.uber.com/ul/?action=setPickup&pickup[latitude]=${sourcePoint.lat}&pickup[longitude]=${sourcePoint.lon}&pickup[nickname]=${encodeURIComponent(
          sourcePoint.label
        )}&dropoff[latitude]=${destPoint.lat}&dropoff[longitude]=${destPoint.lon}&dropoff[nickname]=${encodeURIComponent(
          destPoint.label
        )}`
      : "#";

  return (
    <div style={{ width: "100vw", height: "100vh", position: "relative" }}>
      <div style={panelStyle}>
        <strong style={{ fontSize: 15, marginBottom: 8, display: "block" }}>Trip Planner</strong>

        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 11, color: "#6B7280", marginBottom: 3 }}>FROM</div>
          {sourceMode === "live" ? (
            <div
              style={{ ...searchInputStyle, display: "flex", alignItems: "center", cursor: "pointer" }}
              onClick={() => setSourceMode("search")}
            >
              📍 {sourcePoint?.label || "Live Location — locating..."}
            </div>
          ) : (
            <LocationSearchBox
              placeholder="Kahan se? (type karo)"
              value={sourcePoint?.label || ""}
              onSelect={(p) => setSourcePoint(p)}
            />
          )}
          {sourceMode === "search" && (
            <button onClick={useLiveLocation} style={{ ...btnStyle(false), fontSize: 11, marginTop: 4 }}>
              📍 Live Location use karo
            </button>
          )}
        </div>

        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 11, color: "#6B7280", marginBottom: 3 }}>TO</div>
          <LocationSearchBox
            placeholder="Kahan jana hai? (type karo, ya map pe click karo)"
            value={destPoint?.label || ""}
            onSelect={(p) => setDestPoint(p)}
          />
        </div>

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {(["MORNING", "AFTERNOON", "EVENING", "NIGHT"] as TimeSlot[]).map((slot) => (
            <button key={slot} onClick={() => setTimeSlot(slot)} style={{ ...btnStyle(timeSlot === slot), fontSize: 12 }}>
              {slot}
            </button>
          ))}
        </div>

        {destPoint && (
          <button onClick={resetTrip} style={{ ...btnStyle(false), marginTop: 8, fontSize: 12 }}>
            Trip Clear Karo
          </button>
        )}
      </div>

      {loading && <div style={statusBoxStyle}>Trip plan ho raha hai...</div>}
      {error && <div style={{ ...statusBoxStyle, color: "red" }}>{error}</div>}

      {result && (
        <div style={resultsPanelStyle}>
          <div style={{ display: "flex", gap: 10, marginBottom: 10 }}>
            <div style={cardStyle(activeTab === "bus")} onClick={() => setActiveTab("bus")}>
              <div style={{ fontWeight: 600 }}>🚏 Public Transit</div>
              <div style={{ fontSize: 13, color: "#374151" }}>
                {result.transit_option.total_time_min} min · ₹{result.transit_option.total_fare}
              </div>
            </div>
            <div style={cardStyle(activeTab === "cab")} onClick={() => setActiveTab("cab")}>
              <div style={{ fontWeight: 600 }}>🚕 Cab</div>
              <div style={{ fontSize: 13, color: "#374151" }}>
                {result.cab_option.time_min} min · ₹{result.cab_option.fare}
              </div>
            </div>
          </div>

          {activeTab === "bus" && (
            <div style={{ maxHeight: 300, overflowY: "auto" }}>
              {result.transit_option.steps.map((step, idx) => (
                <div key={idx} style={{ ...stepRowStyle, flexDirection: "column", alignItems: "flex-start" }}>
                  <div style={{ display: "flex", alignItems: "center" }}>
                    <span style={{ fontSize: 18, marginRight: 8 }}>{MODE_ICONS[step.mode]}</span>
                    <span style={{ fontSize: 13 }}>{step.label}</span>
                  </div>
                  {step.mode === "BUS" && step.bus_details && (
                    <div style={{ fontSize: 12, color: "#2563EB", marginLeft: 26, marginTop: 2 }}>
                      🚌 Bus {step.bus_details.route_number} — {step.bus_details.departure_time} →{" "}
                      {step.bus_details.arrival_time} · ₹{step.bus_details.fare}
                      {step.bus_details.fare_is_estimated ? " (est.)" : ""}
                    </div>
                  )}
                </div>
              ))}

              {directBuses && directBuses.direct_bus_options.length > 0 && (
                <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid #E5E7EB" }}>
                  <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>Ya seedhi (direct) bus options:</div>
                  {directBuses.direct_bus_options.map((bus, idx) => (
                    <div key={idx} style={{ fontSize: 12, color: "#374151", padding: "4px 0" }}>
                      Bus <b>{bus.route_number}</b> {bus.headsign ? `(${bus.headsign})` : ""} — {bus.departure_time} →{" "}
                      {bus.arrival_time} · {bus.stops_between} stops · ₹{bus.fare}
                      {bus.fare_is_estimated ? " (est.)" : ""}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {activeTab === "cab" && (
            <div>
              <div style={{ fontSize: 13, marginBottom: 10 }}>
                Distance: <b>{result.cab_option.distance_km} km</b> · Estimated Fare: <b>₹{result.cab_option.fare}</b> ·
                Time: <b>{result.cab_option.time_min} min</b>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <a href={uberLink} target="_blank" rel="noreferrer" style={bookButtonStyle("#000000")}>
                  Book on Uber
                </a>
                <a href="https://www.rapido.bike/" target="_blank" rel="noreferrer" style={bookButtonStyle("#FBBF24")}>
                  Search on Rapido
                </a>
              </div>
              <div style={{ fontSize: 11, color: "#9CA3AF", marginTop: 8 }}>
                Fare estimate hamare historical ride data se hai — Uber/Rapido app mein exact live fare dikhega.
              </div>
            </div>
          )}
        </div>
      )}

      <ReactMapGL
        initialViewState={BENGALURU_CENTER}
        style={{ width: "100%", height: "100%" }}
        mapStyle={MAP_STYLE}
        onClick={handleMapClick}
        cursor="crosshair"
      >
        <NavigationControl position="top-right" />

        {stepFeatures.length > 0 && (
          <Source id="trip-steps" type="geojson" data={stepsGeoJson}>
            <Layer
              id="trip-steps-line"
              type="line"
              paint={{
                "line-color": ["match", ["get", "mode"], "WALK", MODE_COLORS.WALK, "BUS", MODE_COLORS.BUS, "METRO", MODE_COLORS.METRO, "#000"],
                "line-width": ["match", ["get", "mode"], "WALK", 3, 5],
                "line-dasharray": ["match", ["get", "mode"], "WALK", ["literal", [2, 2]], ["literal", [1, 0]]],
              }}
            />
          </Source>
        )}

        {sourcePoint && (
          <Marker latitude={sourcePoint.lat} longitude={sourcePoint.lon}>
            <div style={pinStyle("#DC2626")}>A</div>
          </Marker>
        )}
        {destPoint && (
          <Marker latitude={destPoint.lat} longitude={destPoint.lon}>
            <div style={pinStyle("#EA580C")}>B</div>
          </Marker>
        )}
      </ReactMapGL>
    </div>
  );
}

function pinStyle(color: string): React.CSSProperties {
  return {
    width: 28, height: 28, borderRadius: "50% 50% 50% 0", background: color, transform: "rotate(-45deg)",
    display: "flex", alignItems: "center", justifyContent: "center", color: "white", fontWeight: 700, fontSize: 13,
    border: "2px solid white", boxShadow: "0 2px 6px rgba(0,0,0,0.3)",
  };
}

function btnStyle(active: boolean): React.CSSProperties {
  return { padding: "6px 12px", borderRadius: 6, border: "none", background: active ? "#2563EB" : "#E5E7EB", color: active ? "white" : "#111827", cursor: "pointer", fontSize: 13 };
}

function cardStyle(active: boolean): React.CSSProperties {
  return { flex: 1, padding: "10px 14px", borderRadius: 8, background: active ? "#EFF6FF" : "#F9FAFB", border: active ? "1px solid #93C5FD" : "1px solid #E5E7EB", cursor: "pointer" };
}

function bookButtonStyle(bg: string): React.CSSProperties {
  return { flex: 1, textAlign: "center", padding: "10px", borderRadius: 8, background: bg, color: bg === "#FBBF24" ? "#111827" : "white", fontWeight: 600, fontSize: 13, textDecoration: "none" };
}

const stepRowStyle: React.CSSProperties = { display: "flex", alignItems: "center", padding: "6px 0", borderBottom: "1px solid #F3F4F6" };

const panelStyle: React.CSSProperties = {
  position: "absolute", top: 16, left: 16, zIndex: 10, background: "white", padding: "14px 18px",
  borderRadius: 10, boxShadow: "0 2px 10px rgba(0,0,0,0.15)", width: 300,
};

const resultsPanelStyle: React.CSSProperties = {
  position: "absolute", bottom: 20, left: 16, right: 16, maxWidth: 480, maxHeight: "60vh", overflowY: "auto",
  zIndex: 10, background: "white", padding: "16px 20px", borderRadius: 10, boxShadow: "0 2px 12px rgba(0,0,0,0.2)",
};

const statusBoxStyle: React.CSSProperties = {
  position: "absolute", top: 16, right: 16, zIndex: 10, background: "white", padding: "8px 14px",
  borderRadius: 8, boxShadow: "0 2px 8px rgba(0,0,0,0.15)", fontSize: 14,
};

const searchInputStyle: React.CSSProperties = {
  width: "100%", padding: "8px 10px", borderRadius: 6, border: "1px solid #D1D5DB", fontSize: 13, boxSizing: "border-box",
};

const suggestionBoxStyle: React.CSSProperties = {
  position: "absolute", top: "100%", left: 0, right: 0, background: "white", border: "1px solid #E5E7EB",
  borderRadius: 6, marginTop: 2, zIndex: 20, maxHeight: 180, overflowY: "auto", boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
};

const suggestionItemStyle: React.CSSProperties = {
  padding: "8px 10px", fontSize: 12, cursor: "pointer", borderBottom: "1px solid #F3F4F6",
};