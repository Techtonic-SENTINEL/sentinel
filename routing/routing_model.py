# SENTINEL routing/ · Routing Model
# Dijkstra / A* on the OpenStreetMap road graph; blocked edges removed,
# flooded edges time-penalised, travel-time matrix rebuilt on each closure.
import math
import networkx as nx


def straight_line_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_graph(path):
    # Loads the GraphML road network saved by Notebook 1 (no OSMnx needed)
    G = nx.read_graphml(path, force_multigraph=True)
    G = nx.relabel_nodes(G, {n: int(n) for n in G.nodes})
    for _, d in G.nodes(data=True):
        d["x"], d["y"] = float(d["x"]), float(d["y"])
    for _, _, _, d in G.edges(keys=True, data=True):
        for a in ("length", "speed_kph", "travel_time"):
            if a in d:
                d[a] = float(d[a])
    return G


def close_road(graph, u, v):
    for a, b in [(u, v), (v, u)]:
        while graph.has_edge(a, b):
            graph.remove_edge(a, b)


def road_state_key(events, minute):
    # Identifies which version of the road network applies at this minute
    floods = [i for i, e in enumerate(events) if e["type"] == "flood" and e["t"] <= minute]
    closures = [i for i, e in enumerate(events) if e["type"] == "road_closed" and e["t"] <= minute]
    return (floods[-1] if floods else None, tuple(closures))


def live_graph(G, events, minute, flood_center):
    # The road network as it is at `minute`: flooded roads slower, closed roads removed
    Gl = G.copy()
    floods = [e for e in events if e["type"] == "flood" and e["t"] <= minute]
    if floods:
        f = floods[-1]
        for u, v, k, d in Gl.edges(keys=True, data=True):
            mid_lat = (Gl.nodes[u]["y"] + Gl.nodes[v]["y"]) / 2
            mid_lon = (Gl.nodes[u]["x"] + Gl.nodes[v]["x"]) / 2
            if straight_line_m(mid_lat, mid_lon, flood_center[0], flood_center[1]) <= f["radius_m"]:
                d["travel_time"] = d["travel_time"] * f["factor"]
    for e in events:
        if e["type"] == "road_closed" and e["t"] <= minute:
            close_road(Gl, e["u"], e["v"])
    return Gl


class TravelTimes:
    # Travel-time matrix for one version of the road network (Dijkstra, cached)
    def __init__(self, graph):
        self.G = graph
        self.R = graph.reverse(copy=False)
        self._from, self._to = {}, {}

    def from_node(self, n):
        if n not in self._from:
            self._from[n] = nx.single_source_dijkstra_path_length(self.G, n, weight="travel_time")
        return self._from[n]

    def to_node(self, n):
        if n not in self._to:
            self._to[n] = nx.single_source_dijkstra_path_length(self.R, n, weight="travel_time")
        return self._to[n]

    def minutes(self, a, b):
        # Minutes from junction a to junction b, or None if no road connects them
        s = self.to_node(b).get(a)
        return None if s is None else s / 60


def astar_route(graph, a, b):
    top_speed = max(d["speed_kph"] for _, _, d in graph.edges(data=True)) / 3.6

    def guess(u, v):
        return straight_line_m(graph.nodes[u]["y"], graph.nodes[u]["x"],
                               graph.nodes[v]["y"], graph.nodes[v]["x"]) / top_speed
    try:
        return nx.astar_path(graph, a, b, heuristic=guess, weight="travel_time")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
