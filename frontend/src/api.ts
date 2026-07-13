import axios from "axios";

const API_BASE_URL = "http://127.0.0.1:8000";

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
});

export interface TransitNode {
  id: number;
  name: string;
  type: "BMTC_BUS_STOP" | "METRO_STATION";
  code: string | null;
  latitude: number;
  longitude: number;
  metadata?: Record<string, unknown>;
}

export interface TransitNodesResponse {
  count: number;
  nodes: TransitNode[];
}

export async function fetchTransitNodes(
  nodeType?: "BMTC_BUS_STOP" | "METRO_STATION",
  limit: number = 1000
): Promise<TransitNodesResponse> {
  const params: Record<string, string | number> = { limit };
  if (nodeType) params.node_type = nodeType;
  const response = await apiClient.get<TransitNodesResponse>("/api/transit/nodes", { params });
  return response.data;
}

export type TripMode = "WALK" | "BUS" | "METRO";

export interface TripStep {
  mode: TripMode;
  label: string;
  distance_km: number;
  coordinates: [number, number][];
  bus_details?: {
    route_number: string;
    departure_time: string;
    arrival_time: string;
    fare: number;
    fare_is_estimated: boolean;
  };
}

export interface TransitOption {
  steps: TripStep[];
  total_distance_km: number;
  total_fare: number;
  total_time_min: number;
  congestion_factor: number;
}

export interface CabOption {
  distance_km: number;
  fare: number;
  time_min: number;
  congestion_factor: number;
  route_coordinates: [number, number][] | null;
}

export interface TripPlanResult {
  time_slot: string;
  transit_option: TransitOption;
  cab_option: CabOption;
}

export async function planTrip(
  sourceLat: number,
  sourceLon: number,
  destLat: number,
  destLon: number,
  timeSlot: "MORNING" | "AFTERNOON" | "EVENING" | "NIGHT"
): Promise<TripPlanResult> {
  const response = await apiClient.get<TripPlanResult>("/api/routing/plan-trip", {
    params: {
      source_lat: sourceLat,
      source_lon: sourceLon,
      dest_lat: destLat,
      dest_lon: destLon,
      time_slot: timeSlot,
    },
  });
  return response.data;
}

export interface DirectBusOption {
  route_number: string;
  route_name: string;
  headsign: string | null;
  departure_time: string;
  arrival_time: string;
  stops_between: number;
  fare: number;
  fare_is_estimated: boolean;
  trip_id?: string;
  real_road_shape?: [number, number][];
}

export interface DirectBusResult {
  nearest_source_stop: { stop_id: string; stop_name: string; distance_km: number };
  nearest_dest_stop: { stop_id: string; stop_name: string; distance_km: number };
  direct_bus_options: DirectBusOption[];
  note: string;
}

export async function fetchDirectBuses(
  sourceLat: number,
  sourceLon: number,
  destLat: number,
  destLon: number,
  afterTime: string = "00:00:00"
): Promise<DirectBusResult> {
  const response = await apiClient.get<DirectBusResult>("/api/routing/direct-buses", {
    params: { source_lat: sourceLat, source_lon: sourceLon, dest_lat: destLat, dest_lon: destLon, after_time: afterTime },
  });
  return response.data;
}