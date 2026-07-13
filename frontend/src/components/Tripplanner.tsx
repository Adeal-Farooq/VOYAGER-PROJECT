import { useState, useCallback, useEffect, useRef } from "react";
import ReactMapGL, { Source, Layer, Marker, NavigationControl, type MapRef, type MapLayerMouseEvent } from "react-map-gl/maplibre";
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
      attribution: "© OpenStreetMap contributors, routing via OSRM",
    },
  },
  layers: [{ id: "osm-tiles-layer", type: "raster" as const, source: "osm-tiles" }],
};

type TimeSlot = "MORNING" | "AFTERNOON" | "EVENING" | "NIGHT";
type Point = { lat: number; lon: number; label: string };

const MODE_COLORS: Record<string, string> = { WALK: "#9AA0A6", BUS: "#4285F4", METRO: "#0F9D58" };
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

function distanceMeters(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const R = 6371000;
  const dLat = ((b.lat - a.lat) * Math.PI) / 180;
  const dLon = ((b.lon - a.lon) * Math.PI) / 180;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.asin(Math.sqrt(s));
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
  const mapRef = useRef<MapRef | null>(null);
  const [sourcePoint, setSourcePoint] = useState<Point | null>(null);
  const [destPoint, setDestPoint] = useState<Point | null>(null);
  const [sourceMode, setSourceMode] = useState<"live" | "search">("live");
  const [timeSlot, setTimeSlot] = useState<TimeSlot>("MORNING");
  const [result, setResult] = useState<TripPlanResult | null>(null);
  const [directBuses, setDirectBuses] = useState<DirectBusResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"bus" | "cab">("bus");
  const [navigating, setNavigating] = useState(false);
  const [currentStepIdx, setCurrentStepIdx] = useState(0);
  const [livePos, setLivePos] = useState<Point | null>(null);

  const lastPlannedSourceRef = useRef<Point | null>(null);

  useEffect(() => {
    if (!navigator.geolocation) return;
    const watchId = navigator.geolocation.watchPosition(
      (pos) => {
        const p = { lat: pos.coords.latitude, lon: pos.coords.longitude, label: "Live Location" };
        setLivePos(p);
        setSourcePoint((prev) => (sourceMode !== "live" ? prev : p));
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
      if (last && distanceMeters(last, sourcePoint) < 40) return;
      lastPlannedSourceRef.current = sourcePoint;
      runPlanTrip(sourcePoint, destPoint, timeSlot);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourcePoint, destPoint]);

  useEffect(() => {
    if (sourcePoint && destPoint) runPlanTrip(sourcePoint, destPoint, timeSlot);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeSlot]);

  // Navigation mode: live position ko route ke against track karo, auto-advance steps
  useEffect(() => {
    if (!navigating || !result || !livePos) return;
    const steps = result.transit_option.steps;
    const step = steps[currentStepIdx];
    if (!step) return;
    const stepEnd = step.coordinates[step.coordinates.length - 1];
    const distToEnd = distanceMeters(livePos, { lat: stepEnd[1], lon: stepEnd[0] });
    if (distToEnd < 35 && currentStepIdx < steps.length - 1) {
      setCurrentStepIdx((i) => i + 1);
    }
    // Map ko live position pe follow karao
    mapRef.current?.getMap().easeTo({ center: [livePos.lon, livePos.lat], duration: 500 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [livePos, navigating]);

  async function runPlanTrip(src: Point, dst: Point, slot: TimeSlot) {
    setLoading(true);
    setError(null);
    try {
      const data = await planTrip(src.lat, src.lon, dst.lat, dst.lon, slot);
      setResult(data);
      setActiveTab("bus");
      setCurrentStepIdx(0);
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
    if (navigating) return;
    const { lat, lng } = e.lngLat;
    setDestPoint({ lat, lon: lng, label: `${lat.toFixed(4)}, ${lng.toFixed(4)}` });
  }, [navigating]);

  function resetTrip() {
    setDestPoint(null);
    setResult(null);
    setDirectBuses(null);
    setError(null);
    setNavigating(false);
    setCurrentStepIdx(0);
  }

  function useLiveLocation() {
    setSourceMode("live");
    navigator.geolocation.getCurrentPosition(
      (pos) => setSourcePoint({ lat: pos.coords.latitude, lon: pos.coords.longitude, label: "Live Location" }),
      () => setError("Live location nahi mila — permission check karo.")
    );
  }

  function startNavigation() {
    setNavigating(true);
    setCurrentStepIdx(0);
  }

  function endNavigation() {
    setNavigating(false);
  }

  const stepFeatures = result
    ? result.transit_option.steps.map((step, idx) => ({
        type: "Feature" as const,
        properties: { mode: step.mode, idx, isPast: idx < currentStepIdx, isCurrent: idx === currentStepIdx },
        geometry: { type: "LineString" as const, coordinates: step.coordinates },
      }))
    : [];
  const stepsGeoJson = { type: "FeatureCollection" as const, features: stepFeatures };

  const cabRouteGeoJson = result?.cab_option.route_coordinates
    ? {
        type: "Feature" as const,
        properties: {},
        geometry: { type: "LineString" as const, coordinates: result.cab_option.route_coordinates },
      }
    : null;

  const uberLink =
    sourcePoint && destPoint
      ? `https://m.uber.com/ul/?action=setPickup&pickup[latitude]=${sourcePoint.lat}&pickup[longitude]=${sourcePoint.lon}&pickup[nickname]=${encodeURIComponent(
          sourcePoint.label
        )}&dropoff[latitude]=${destPoint.lat}&dropoff[longitude]=${destPoint.lon}&dropoff[nickname]=${encodeURIComponent(
          destPoint.label
        )}`
      : "#";

  const currentStep = result?.transit_option.steps[currentStepIdx];

  return (
    <div style={{ width: "100vw", height: "100vh", position: "relative", fontFamily: "'Google Sans', Roboto, Arial, sans-serif" }}>
      {/* Search panel — hidden during navigation */}
      {!navigating && (
        <div style={panelStyle}>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 10, color: "#202124" }}>Trip Planner</div>

          <div style={{ marginBottom: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={dotStyle("#4285F4")} />
              {sourceMode === "live" ? (
                <div style={{ ...searchInputStyle, cursor: "pointer" }} onClick={() => setSourceMode("search")}>
                  {sourcePoint?.label || "Locating..."}
                </div>
              ) : (
                <LocationSearchBox placeholder="Kahan se?" value={sourcePoint?.label || ""} onSelect={setSourcePoint} />
              )}
            </div>
            {sourceMode === "search" && (
              <button onClick={useLiveLocation} style={{ ...chipStyle, marginTop: 6, marginLeft: 22 }}>
                📍 Use live location
              </button>
            )}
          </div>

          <div style={{ marginBottom: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={dotStyle("#EA4335")} />
              <LocationSearchBox placeholder="Kahan jana hai?" value={destPoint?.label || ""} onSelect={setDestPoint} />
            </div>
          </div>

          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {(["MORNING", "AFTERNOON", "EVENING", "NIGHT"] as TimeSlot[]).map((slot) => (
              <button key={slot} onClick={() => setTimeSlot(slot)} style={chipStyle2(timeSlot === slot)}>
                {slot}
              </button>
            ))}
          </div>

          {destPoint && (
            <button onClick={resetTrip} style={{ ...chipStyle, marginTop: 10 }}>
              ✕ Clear trip
            </button>
          )}
        </div>
      )}

      {loading && <div style={statusBoxStyle}>Planning your trip...</div>}
      {error && !navigating && <div style={{ ...statusBoxStyle, color: "#D93025" }}>{error}</div>}

      {/* NAVIGATION MODE — immersive turn-by-turn style banner */}
      {navigating && currentStep && (
        <div style={navBannerStyle}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ fontSize: 28 }}>{MODE_ICONS[currentStep.mode]}</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 15, fontWeight: 600 }}>{currentStep.label}</div>
              {currentStep.mode === "BUS" && currentStep.bus_details && (
                <div style={{ fontSize: 12, color: "#AECBFA", marginTop: 2 }}>
                  Bus {currentStep.bus_details.route_number} · {currentStep.bus_details.departure_time} → {currentStep.bus_details.arrival_time}
                </div>
              )}
            </div>
            <button onClick={endNavigation} style={endTripButtonStyle}>End</button>
          </div>
          <div style={{ fontSize: 11, color: "#9AA0A6", marginTop: 6 }}>
            Step {currentStepIdx + 1} of {result?.transit_option.steps.length}
          </div>
        </div>
      )}

      {/* Bottom results / details sheet */}
      {result && !navigating && (
        <div style={resultsPanelStyle}>
          <div style={{ display: "flex", gap: 0, marginBottom: 12, borderBottom: "1px solid #3C4043" }}>
            <button onClick={() => setActiveTab("bus")} style={tabStyle(activeTab === "bus")}>
              🚏 Transit · {result.transit_option.total_time_min} min · ₹{result.transit_option.total_fare}
            </button>
            <button onClick={() => setActiveTab("cab")} style={tabStyle(activeTab === "cab")}>
              🚕 Cab · {result.cab_option.time_min} min · ₹{result.cab_option.fare}
            </button>
          </div>

          {activeTab === "bus" && (
            <div>
              <button onClick={startNavigation} style={startButtonStyle}>▶ Start</button>
              <div style={{ maxHeight: 260, overflowY: "auto", marginTop: 12 }}>
                {result.transit_option.steps.map((step, idx) => (
                  <div key={idx} style={{ ...stepRowStyle, flexDirection: "column", alignItems: "flex-start" }}>
                    <div style={{ display: "flex", alignItems: "center" }}>
                      <span style={{ fontSize: 18, marginRight: 10 }}>{MODE_ICONS[step.mode]}</span>
                      <span style={{ fontSize: 13, color: "#E8EAED" }}>{step.label}</span>
                    </div>
                    {step.mode === "BUS" && step.bus_details && (
                      <div style={{ fontSize: 12, color: "#8AB4F8", marginLeft: 28, marginTop: 2 }}>
                        🚌 Bus {step.bus_details.route_number} — {step.bus_details.departure_time} → {step.bus_details.arrival_time} · ₹{step.bus_details.fare}
                        {step.bus_details.fare_is_estimated ? " (est.)" : ""}
                      </div>
                    )}
                  </div>
                ))}

                {directBuses && directBuses.direct_bus_options.length > 0 && (
                  <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid #3C4043" }}>
                    <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 6, color: "#9AA0A6" }}>DIRECT OPTIONS</div>
                    {directBuses.direct_bus_options.map((bus, idx) => (
                      <div key={idx} style={{ fontSize: 12, color: "#BDC1C6", padding: "4px 0" }}>
                        Bus <b style={{ color: "#E8EAED" }}>{bus.route_number}</b> {bus.headsign ? `(${bus.headsign})` : ""} — {bus.departure_time} → {bus.arrival_time} · {bus.stops_between} stops · ₹{bus.fare}
                        {bus.fare_is_estimated ? " (est.)" : ""}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {activeTab === "cab" && (
            <div>
              <div style={{ fontSize: 13, marginBottom: 12, color: "#E8EAED" }}>
                {result.cab_option.distance_km} km · ₹{result.cab_option.fare} · {result.cab_option.time_min} min
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <a href={uberLink} target="_blank" rel="noreferrer" style={bookButtonStyle("#FFFFFF", "#000")}>Book on Uber</a>
                <a href="https://www.rapido.bike/" target="_blank" rel="noreferrer" style={bookButtonStyle("#111827", "#FBBF24")}>Search on Rapido</a>
              </div>
              <div style={{ fontSize: 11, color: "#9AA0A6", marginTop: 10 }}>
                Fare estimate hamare historical ride data se hai — app mein exact live fare dikhega.
              </div>
            </div>
          )}
        </div>
      )}

      <ReactMapGL
        ref={mapRef}
        initialViewState={BENGALURU_CENTER}
        style={{ width: "100%", height: "100%" }}
        mapStyle={MAP_STYLE}
        onClick={handleMapClick}
        cursor={navigating ? "default" : "crosshair"}
      >
        <NavigationControl position="top-right" />

        {stepFeatures.length > 0 && activeTab === "bus" && (
          <Source id="trip-steps" type="geojson" data={stepsGeoJson}>
            <Layer
              id="trip-steps-line"
              type="line"
              paint={{
                "line-color": [
                  "case",
                  ["get", "isPast"], "#5F6368",
                  ["match", ["get", "mode"], "WALK", MODE_COLORS.WALK, "BUS", MODE_COLORS.BUS, "METRO", MODE_COLORS.METRO, "#000"],
                ],
                "line-width": ["case", ["get", "isCurrent"], 6, ["match", ["get", "mode"], "WALK", 3, 5]],
                "line-opacity": ["case", ["get", "isPast"], 0.4, 0.9],
                "line-dasharray": ["match", ["get", "mode"], "WALK", ["literal", [2, 2]], ["literal", [1, 0]]],
              }}
            />
          </Source>
        )}

        {cabRouteGeoJson && activeTab === "cab" && (
          <Source id="cab-route" type="geojson" data={cabRouteGeoJson}>
            <Layer id="cab-route-line" type="line" paint={{ "line-color": "#111827", "line-width": 5, "line-opacity": 0.85 }} />
          </Source>
        )}

        {sourcePoint && !navigating && (
          <Marker latitude={sourcePoint.lat} longitude={sourcePoint.lon}>
            <div style={pinStyle("#4285F4")}>A</div>
          </Marker>
        )}
        {destPoint && (
          <Marker latitude={destPoint.lat} longitude={destPoint.lon}>
            <div style={pinStyle("#EA4335")}>B</div>
          </Marker>
        )}

        {/* Live tracking blue dot (Google Maps style) — navigation mode mein continuously move hota hai */}
        {livePos && (
          <Marker latitude={livePos.lat} longitude={livePos.lon}>
            <div style={liveDotOuterStyle}>
              <div style={liveDotInnerStyle} />
            </div>
          </Marker>
        )}
      </ReactMapGL>
    </div>
  );
}

function pinStyle(color: string): React.CSSProperties {
  return {
    width: 26, height: 26, borderRadius: "50% 50% 50% 0", background: color, transform: "rotate(-45deg)",
    display: "flex", alignItems: "center", justifyContent: "center", color: "white", fontWeight: 700, fontSize: 12,
    border: "2px solid white", boxShadow: "0 2px 6px rgba(0,0,0,0.3)",
  };
}

function dotStyle(color: string): React.CSSProperties {
  return { width: 10, height: 10, borderRadius: "50%", background: color, flexShrink: 0 };
}

const liveDotOuterStyle: React.CSSProperties = {
  width: 22, height: 22, borderRadius: "50%", background: "rgba(66,133,244,0.25)",
  display: "flex", alignItems: "center", justifyContent: "center",
};
const liveDotInnerStyle: React.CSSProperties = {
  width: 14, height: 14, borderRadius: "50%", background: "#4285F4", border: "3px solid white",
  boxShadow: "0 0 4px rgba(0,0,0,0.4)",
};

function chipStyle2(active: boolean): React.CSSProperties {
  return {
    padding: "6px 12px", borderRadius: 16, border: "1px solid " + (active ? "#4285F4" : "#DADCE0"),
    background: active ? "#E8F0FE" : "white", color: active ? "#1967D2" : "#3C4043", cursor: "pointer", fontSize: 12, fontWeight: 500,
  };
}

const chipStyle: React.CSSProperties = {
  padding: "5px 10px", borderRadius: 14, border: "1px solid #DADCE0", background: "white", color: "#3C4043", cursor: "pointer", fontSize: 11,
};

function tabStyle(active: boolean): React.CSSProperties {
  return {
    flex: 1, padding: "10px 8px", background: "transparent", border: "none", cursor: "pointer",
    borderBottom: active ? "2px solid #8AB4F8" : "2px solid transparent",
    color: active ? "#8AB4F8" : "#9AA0A6", fontSize: 13, fontWeight: 600,
  };
}

function bookButtonStyle(color: string, bg: string): React.CSSProperties {
  return { flex: 1, textAlign: "center", padding: "11px", borderRadius: 24, background: bg, color, fontWeight: 600, fontSize: 13, textDecoration: "none" };
}

const startButtonStyle: React.CSSProperties = {
  width: "100%", padding: "12px", borderRadius: 24, background: "#8AB4F8", color: "#202124",
  border: "none", fontWeight: 700, fontSize: 14, cursor: "pointer",
};

const endTripButtonStyle: React.CSSProperties = {
  padding: "8px 16px", borderRadius: 20, background: "#EA4335", color: "white", border: "none", fontWeight: 600, fontSize: 12, cursor: "pointer",
};

const stepRowStyle: React.CSSProperties = { display: "flex", alignItems: "center", padding: "8px 0", borderBottom: "1px solid #3C4043" };

const panelStyle: React.CSSProperties = {
  position: "absolute", top: 16, left: 16, zIndex: 10, background: "white", padding: "16px",
  borderRadius: 12, boxShadow: "0 1px 6px rgba(32,33,36,0.28)", width: 300,
};

const resultsPanelStyle: React.CSSProperties = {
  position: "absolute", bottom: 0, left: 0, right: 0, maxHeight: "48vh", overflowY: "auto",
  zIndex: 10, background: "#202124", color: "#E8EAED", padding: "16px 20px 24px",
  borderRadius: "16px 16px 0 0", boxShadow: "0 -2px 16px rgba(0,0,0,0.4)",
};

const navBannerStyle: React.CSSProperties = {
  position: "absolute", top: 16, left: 16, right: 16, zIndex: 10, background: "#202124", color: "white",
  padding: "16px 18px", borderRadius: 12, boxShadow: "0 2px 12px rgba(0,0,0,0.4)",
};

const statusBoxStyle: React.CSSProperties = {
  position: "absolute", top: 16, right: 16, zIndex: 10, background: "white", padding: "8px 14px",
  borderRadius: 8, boxShadow: "0 1px 6px rgba(32,33,36,0.28)", fontSize: 13,
};

const searchInputStyle: React.CSSProperties = {
  flex: 1, padding: "8px 10px", borderRadius: 8, border: "1px solid #DADCE0", fontSize: 13, boxSizing: "border-box", background: "#F1F3F4",
};

const suggestionBoxStyle: React.CSSProperties = {
  position: "absolute", top: "100%", left: 0, right: 0, background: "white", border: "1px solid #DADCE0",
  borderRadius: 8, marginTop: 4, zIndex: 20, maxHeight: 180, overflowY: "auto", boxShadow: "0 2px 10px rgba(0,0,0,0.15)",
};

const suggestionItemStyle: React.CSSProperties = {
  padding: "9px 12px", fontSize: 12, cursor: "pointer", borderBottom: "1px solid #F1F3F4", color: "#3C4043",
};