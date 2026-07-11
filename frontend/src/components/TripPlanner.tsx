import { useState, useCallback } from "react";
import ReactMapGL, { Source, Layer, Marker, NavigationControl, type MapLayerMouseEvent } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import { planTrip, fetchDirectBuses, type TripPlanResult, type DirectBusResult } from "../api";

const BENGALURU_CENTER = { latitude: 12.9716, longitude: 77.5946, zoom: 12 };

// Real OpenStreetMap raster tiles (free, no API key — as per project's zero-cost OSM requirement)
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
  layers: [
    {
      id: "osm-layer",
      type: "raster" as const,
      source: "osm-tiles",
    },
  ],
};

type TimeSlot = "MORNING" | "AFTERNOON" | "EVENING" | "NIGHT";

const MODE_COLORS: Record<string, string> = {
  WALK: "#6B7280",
  BUS: "#2563EB",
  METRO: "#16A34A",
};

const MODE_ICONS: Record<string, string> = {
  WALK: "🚶",
  BUS: "🚌",
  METRO: "🚇",
};

export default function TripPlanner() {
  const [sourcePoint, setSourcePoint] = useState<{ lat: number; lon: number } | null>(null);
  const [destPoint, setDestPoint] = useState<{ lat: number; lon: number } | null>(null);
  const [timeSlot, setTimeSlot] = useState<TimeSlot>("MORNING");
  const [result, setResult] = useState<TripPlanResult | null>(null);
  const [directBuses, setDirectBuses] = useState<DirectBusResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleMapClick = useCallback(
    (e: MapLayerMouseEvent) => {
      const { lat, lng } = e.lngLat;

      if (!sourcePoint) {
        setSourcePoint({ lat, lon: lng });
        setDestPoint(null);
        setResult(null);
        setError(null);
      } else if (!destPoint) {
        const dest = { lat, lon: lng };
        setDestPoint(dest);
        runPlanTrip(sourcePoint, dest, timeSlot);
      } else {
        setSourcePoint({ lat, lon: lng });
        setDestPoint(null);
        setResult(null);
        setError(null);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sourcePoint, destPoint, timeSlot]
  );

  async function runPlanTrip(src: { lat: number; lon: number }, dst: { lat: number; lon: number }, slot: TimeSlot) {
    setLoading(true);
    setError(null);
    try {
      const data = await planTrip(src.lat, src.lon, dst.lat, dst.lon, slot);
      setResult(data);
    } catch (err) {
      setError("Trip plan nahi ban paya. Shayad is area mein transit nodes nahi hain.");
      console.error(err);
    } finally {
      setLoading(false);
    }

    // Direct bus options alag se fetch karo (yeh fail ho sakta hai bina overall trip plan ko todE)
    try {
      const buses = await fetchDirectBuses(src.lat, src.lon, dst.lat, dst.lon);
      setDirectBuses(buses);
    } catch (err) {
      setDirectBuses(null);
      console.error("Direct bus fetch failed:", err);
    }
  }

  function handleTimeSlotChange(slot: TimeSlot) {
    setTimeSlot(slot);
    if (sourcePoint && destPoint) {
      runPlanTrip(sourcePoint, destPoint, slot);
    }
  }

  function resetTrip() {
    setSourcePoint(null);
    setDestPoint(null);
    setResult(null);
    setDirectBuses(null);
    setError(null);
  }

  // Har step ko alag GeoJSON Feature banao, color mode ke hisaab se
  const stepFeatures = result
    ? result.transit_option.steps.map((step, idx) => ({
        type: "Feature" as const,
        properties: { mode: step.mode, idx },
        geometry: { type: "LineString" as const, coordinates: step.coordinates },
      }))
    : [];

  const stepsGeoJson = { type: "FeatureCollection" as const, features: stepFeatures };

  return (
    <div style={{ width: "100vw", height: "100vh", position: "relative" }}>
      {/* Top control panel */}
      <div style={panelStyle}>
        <strong style={{ fontSize: 15 }}>Trip Planner</strong>
        <div style={{ fontSize: 12, color: "#6B7280", marginTop: 4 }}>
          {!sourcePoint && "Map pe kahi bhi click karo — starting point"}
          {sourcePoint && !destPoint && "Ab destination click karo"}
          {sourcePoint && destPoint && "Trip plan ho gaya — neeche dekho"}
        </div>
        <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap" }}>
          {(["MORNING", "AFTERNOON", "EVENING", "NIGHT"] as TimeSlot[]).map((slot) => (
            <button key={slot} onClick={() => handleTimeSlotChange(slot)} style={btnStyle(timeSlot === slot)}>
              {slot}
            </button>
          ))}
        </div>
        {(sourcePoint || destPoint) && (
          <button onClick={resetTrip} style={{ ...btnStyle(false), marginTop: 8 }}>
            Reset Trip
          </button>
        )}
      </div>

      {loading && <div style={statusBoxStyle}>Trip plan ho raha hai...</div>}
      {error && <div style={{ ...statusBoxStyle, color: "red" }}>{error}</div>}

      {/* Bottom results panel */}
      {result && (
        <div style={resultsPanelStyle}>
          <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
            {/* Transit option card */}
            <div style={cardStyle(true)}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>🚏 Public Transit</div>
              <div style={{ fontSize: 13, color: "#374151" }}>
                {result.transit_option.total_time_min} min · ₹{result.transit_option.total_fare} ·{" "}
                {result.transit_option.total_distance_km} km
              </div>
            </div>
            {/* Cab option card */}
            <div style={cardStyle(false)}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>🚕 Cab</div>
              <div style={{ fontSize: 13, color: "#374151" }}>
                {result.cab_option.time_min} min · ₹{result.cab_option.fare} · {result.cab_option.distance_km} km
              </div>
            </div>
          </div>

          <div style={{ maxHeight: 180, overflowY: "auto" }}>
            {result.transit_option.steps.map((step, idx) => (
              <div key={idx} style={stepRowStyle}>
                <span style={{ fontSize: 18, marginRight: 8 }}>{MODE_ICONS[step.mode]}</span>
                <span style={{ fontSize: 13 }}>{step.label}</span>
              </div>
            ))}
          </div>

          {directBuses && directBuses.direct_bus_options.length > 0 && (
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid #E5E7EB" }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
                🚌 Direct Buses (real BMTC schedule)
              </div>
              <div style={{ maxHeight: 140, overflowY: "auto" }}>
                {directBuses.direct_bus_options.map((bus, idx) => (
                  <div key={idx} style={{ ...stepRowStyle, flexDirection: "column", alignItems: "flex-start" }}>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>
                      Bus {bus.route_number} {bus.headsign ? `→ ${bus.headsign}` : ""}
                    </div>
                    <div style={{ fontSize: 12, color: "#6B7280" }}>
                      {bus.departure_time} → {bus.arrival_time} · {bus.stops_between} stops · ₹
                      {bus.fare}
                      {bus.fare_is_estimated ? " (est.)" : ""}
                    </div>
                  </div>
                ))}
              </div>
              <div style={{ fontSize: 11, color: "#9CA3AF", marginTop: 6 }}>{directBuses.note}</div>
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
                "line-color": [
                  "match",
                  ["get", "mode"],
                  "WALK", MODE_COLORS.WALK,
                  "BUS", MODE_COLORS.BUS,
                  "METRO", MODE_COLORS.METRO,
                  "#000000",
                ],
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
    width: 28,
    height: 28,
    borderRadius: "50% 50% 50% 0",
    background: color,
    transform: "rotate(-45deg)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontWeight: 700,
    fontSize: 13,
    border: "2px solid white",
    boxShadow: "0 2px 6px rgba(0,0,0,0.3)",
  };
}

function btnStyle(active: boolean): React.CSSProperties {
  return {
    padding: "6px 12px",
    borderRadius: 6,
    border: "none",
    background: active ? "#2563EB" : "#E5E7EB",
    color: active ? "white" : "#111827",
    cursor: "pointer",
    fontSize: 13,
  };
}

function cardStyle(highlight: boolean): React.CSSProperties {
  return {
    flex: 1,
    padding: "10px 14px",
    borderRadius: 8,
    background: highlight ? "#EFF6FF" : "#F9FAFB",
    border: highlight ? "1px solid #BFDBFE" : "1px solid #E5E7EB",
  };
}

const stepRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "6px 0",
  borderBottom: "1px solid #F3F4F6",
};

const panelStyle: React.CSSProperties = {
  position: "absolute",
  top: 16,
  left: 16,
  zIndex: 10,
  background: "white",
  padding: "14px 18px",
  borderRadius: 10,
  boxShadow: "0 2px 10px rgba(0,0,0,0.15)",
  minWidth: 260,
};

const resultsPanelStyle: React.CSSProperties = {
  position: "absolute",
  bottom: 20,
  left: 16,
  right: 16,
  maxWidth: 480,
  maxHeight: "70vh",
  overflowY: "auto",
  zIndex: 10,
  background: "white",
  padding: "16px 20px",
  borderRadius: 10,
  boxShadow: "0 2px 12px rgba(0,0,0,0.2)",
};

const statusBoxStyle: React.CSSProperties = {
  position: "absolute",
  top: 16,
  right: 16,
  zIndex: 10,
  background: "white",
  padding: "8px 14px",
  borderRadius: 8,
  boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
  fontSize: 14,
};