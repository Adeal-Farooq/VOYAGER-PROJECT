import { useEffect, useState, useRef, useCallback } from "react";
import ReactMapGL, { Source, Layer, Popup, NavigationControl, type MapRef, type MapLayerMouseEvent } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import { fetchTransitNodes, fetchRoute, type TransitNode, type RouteResult } from "../api";

const BENGALURU_CENTER = { latitude: 12.9716, longitude: 77.5946, zoom: 12 };

// Real OpenStreetMap raster tiles (free, no API key)
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
  layers: [{ id: "osm-layer", type: "raster" as const, source: "osm-tiles" }],
};

type NodeFilter = "BMTC_BUS_STOP" | "METRO_STATION";
type TimeSlot = "MORNING" | "AFTERNOON" | "EVENING" | "NIGHT";

export default function TransitMap() {
  const mapRef = useRef<MapRef | null>(null);
  const [nodes, setNodes] = useState<TransitNode[]>([]);
  const [nodesById, setNodesById] = useState<Map<number, TransitNode>>(new Map());
  const [selectedNode, setSelectedNode] = useState<TransitNode | null>(null);
  const [filter, setFilter] = useState<NodeFilter>("BMTC_BUS_STOP");
  const [timeSlot, setTimeSlot] = useState<TimeSlot>("MORNING");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [routeSource, setRouteSource] = useState<TransitNode | null>(null);
  const [routeTarget, setRouteTarget] = useState<TransitNode | null>(null);
  const [routeResult, setRouteResult] = useState<RouteResult | null>(null);
  const [routeLoading, setRouteLoading] = useState(false);
  const [routeError, setRouteError] = useState<string | null>(null);

  useEffect(() => {
    async function loadNodes() {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchTransitNodes(filter, 3000);
        setNodes(data.nodes);
        setNodesById(new Map(data.nodes.map((n) => [n.id, n])));
      } catch (err) {
        setError("Backend se data load nahi ho paya. Kya server chal raha hai?");
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    loadNodes();
    setRouteSource(null);
    setRouteTarget(null);
    setRouteResult(null);
    setRouteError(null);
  }, [filter]);

  useEffect(() => {
    if (routeSource && routeTarget) {
      calculateRoute(routeSource, routeTarget);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeSlot]);

  async function calculateRoute(source: TransitNode, target: TransitNode) {
    setRouteLoading(true);
    setRouteError(null);
    try {
      const result = await fetchRoute(source.id, target.id, filter, timeSlot);
      setRouteResult(result);
    } catch (err) {
      setRouteError("Route nahi mil paya — shayad yeh points bahut door hain ek doosre se.");
      console.error(err);
    } finally {
      setRouteLoading(false);
    }
  }

  const handleMapClick = useCallback(
    async (e: MapLayerMouseEvent) => {
      const map = mapRef.current?.getMap();
      if (!map) return;

      const features = map.queryRenderedFeatures(e.point, { layers: ["stops-layer"] });
      if (!features.length) return;

      const nodeId = features[0].properties?.id as number;
      const node = nodesById.get(nodeId);
      if (!node) return;

      setSelectedNode(node);

      if (!routeSource) {
        setRouteSource(node);
        setRouteTarget(null);
        setRouteResult(null);
        setRouteError(null);
      } else if (!routeTarget && node.id !== routeSource.id) {
        setRouteTarget(node);
        await calculateRoute(routeSource, node);
      } else {
        setRouteSource(node);
        setRouteTarget(null);
        setRouteResult(null);
        setRouteError(null);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [nodesById, routeSource, routeTarget, filter, timeSlot]
  );

  function resetRoute() {
    setRouteSource(null);
    setRouteTarget(null);
    setRouteResult(null);
    setRouteError(null);
  }

  const stopsGeoJson = {
    type: "FeatureCollection" as const,
    features: nodes.map((node) => ({
      type: "Feature" as const,
      properties: {
        id: node.id,
        isSource: routeSource?.id === node.id,
        isTarget: routeTarget?.id === node.id,
      },
      geometry: { type: "Point" as const, coordinates: [node.longitude, node.latitude] },
    })),
  };

  const routeGeoJson = routeResult
    ? {
        type: "Feature" as const,
        properties: {},
        geometry: {
          type: "LineString" as const,
          coordinates: routeResult.route.path.map((p) => [p.lon, p.lat]),
        },
      }
    : null;

  return (
    <div style={{ width: "100vw", height: "100vh", position: "relative" }}>
      <div
        style={{
          position: "absolute",
          top: 16,
          left: 16,
          zIndex: 10,
          background: "white",
          padding: "12px 16px",
          borderRadius: 8,
          boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={() => setFilter("METRO_STATION")} style={btnStyle(filter === "METRO_STATION")}>
            Metro
          </button>
          <button onClick={() => setFilter("BMTC_BUS_STOP")} style={btnStyle(filter === "BMTC_BUS_STOP")}>
            Bus Stops
          </button>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {(["MORNING", "AFTERNOON", "EVENING", "NIGHT"] as TimeSlot[]).map((slot) => (
            <button
              key={slot}
              onClick={() => setTimeSlot(slot)}
              style={{ ...btnStyle(timeSlot === slot), fontSize: 12, padding: "4px 8px" }}
            >
              {slot}
            </button>
          ))}
        </div>
        <div style={{ fontSize: 12, color: "#6B7280", maxWidth: 220 }}>
          {!routeSource && "Route dekhne ke liye ek stop click karo (source)"}
          {routeSource && !routeTarget && `Source: ${routeSource.name} — ab target stop click karo`}
        </div>
        {(routeSource || routeTarget) && (
          <button onClick={resetRoute} style={{ ...btnStyle(false), fontSize: 12 }}>
            Route Reset Karo
          </button>
        )}
      </div>

      {loading && <div style={statusBoxStyle}>Loading transit data...</div>}
      {error && <div style={{ ...statusBoxStyle, color: "red" }}>{error}</div>}
      {routeLoading && <div style={statusBoxStyle}>Route calculate ho raha hai...</div>}
      {routeError && <div style={{ ...statusBoxStyle, color: "red" }}>{routeError}</div>}

      {routeResult && (
        <div
          style={{
            position: "absolute",
            bottom: 24,
            left: 16,
            zIndex: 10,
            background: "white",
            padding: "16px 20px",
            borderRadius: 8,
            boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
            maxWidth: 320,
          }}
        >
          <strong>
            {routeResult.source.name} → {routeResult.target.name}
          </strong>
          <div style={{ marginTop: 8, fontSize: 14, lineHeight: 1.6 }}>
            Distance: <b>{routeResult.route.total_distance_km} km</b>
            <br />
            Base Fare: <b>₹{routeResult.route.base_fare}</b>
            <br />
            Fare with Surge ({timeSlot}): <b>₹{routeResult.route.estimated_fare}</b>
            <br />
            Congestion: <b>{(routeResult.route.congestion_factor * 100).toFixed(0)}%</b>
            <br />
            Stops in path: <b>{routeResult.route.path.length}</b>
          </div>
        </div>
      )}

      <ReactMapGL
        ref={mapRef}
        initialViewState={BENGALURU_CENTER}
        style={{ width: "100%", height: "100%" }}
        mapStyle={MAP_STYLE}
        onClick={handleMapClick}
        interactiveLayerIds={["stops-layer"]}
        cursor="pointer"
      >
        <NavigationControl position="top-right" />

        {routeGeoJson && (
          <Source id="route" type="geojson" data={routeGeoJson}>
            <Layer
              id="route-line"
              type="line"
              paint={{ "line-color": "#DC2626", "line-width": 4, "line-opacity": 0.85 }}
            />
          </Source>
        )}

        <Source id="stops" type="geojson" data={stopsGeoJson}>
          <Layer id="stops-hitbox" type="circle" paint={{ "circle-radius": 12, "circle-opacity": 0 }} />
          <Layer
            id="stops-layer"
            type="circle"
            paint={{
              "circle-radius": ["case", ["any", ["get", "isSource"], ["get", "isTarget"]], 8, 4],
              "circle-color": [
                "case",
                ["get", "isSource"], "#DC2626",
                ["get", "isTarget"], "#EA580C",
                filter === "METRO_STATION" ? "#16A34A" : "#2563EB",
              ],
              "circle-stroke-width": 1.5,
              "circle-stroke-color": "#ffffff",
            }}
          />
        </Source>

        {selectedNode && !routeResult && (
          <Popup
            latitude={selectedNode.latitude}
            longitude={selectedNode.longitude}
            onClose={() => setSelectedNode(null)}
            closeOnClick={false}
          >
            <div>
              <strong>{selectedNode.name}</strong>
              <br />
              Type: {selectedNode.type}
              {selectedNode.code && (
                <>
                  <br />
                  Code: {selectedNode.code}
                </>
              )}
            </div>
          </Popup>
        )}
      </ReactMapGL>
    </div>
  );
}

function btnStyle(active: boolean): React.CSSProperties {
  return {
    padding: "6px 12px",
    borderRadius: 6,
    border: "none",
    background: active ? "#2563EB" : "#E5E7EB",
    color: active ? "white" : "#111827",
    cursor: "pointer",
    fontSize: 14,
  };
}

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