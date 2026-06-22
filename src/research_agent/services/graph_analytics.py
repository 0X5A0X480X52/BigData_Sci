"""Graph snapshot and ranking algorithms — multi-entity + Louvain.

Phase 3: Multi-entity graph (Paper/Author/Topic/Institution/Venue nodes,
CITES/AUTHORED_BY/HAS_TOPIC/AFFILIATED_WITH/PUBLISHED_IN edges),
Louvain community detection with BFS fallback.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple

from research_agent.core.artifact_store import ArtifactStore
from research_agent.core.models import (
    AuthorNode,
    Corpus,
    GraphSnapshot,
    InstitutionNode,
    MCPResult,
    Paper,
    RunConfig,
    TopicNode,
    VenueNode,
)
from research_agent.core.utils import stable_hash, utc_now_iso


class GraphAnalyticsService:
    def __init__(self, artifact_store: ArtifactStore, config: RunConfig,
                 repository: Any = None) -> None:
        self.artifacts = artifact_store
        self.config = config
        self._repo = repository
        self.snapshots: Dict[str, GraphSnapshot] = {}
        self.metrics: Dict[str, Dict[str, Any]] = {}

    # ── Multi-entity graph construction ──────────────────────

    def build_graph_snapshot(self, corpus: Corpus,
                             parameters: Optional[Dict[str, Any]] = None) -> GraphSnapshot:
        """Build a multi-entity graph snapshot from a corpus.

        Node types: Paper, Author, Topic, Institution, Venue.
        Edge types: CITES, AUTHORED_BY, HAS_TOPIC, AFFILIATED_WITH, PUBLISHED_IN.
        """
        papers = corpus.papers[:self.config.max_graph_nodes]
        paper_ids = {p.work_id for p in papers}

        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        edge_count = 0

        # Extract authors, topics, institutions
        author_map: Dict[str, AuthorNode] = {}
        topic_map: Dict[str, TopicNode] = {}
        inst_map: Dict[str, InstitutionNode] = {}

        for paper in papers:
            # Paper node
            nodes.append({
                "id": paper.work_id, "type": "paper", "title": paper.title,
                "year": paper.publication_year, "cited_by_count": paper.cited_by_count,
            })

            # Author nodes + AUTHORED_BY edges
            for author_name in paper.authors:
                akey = author_name.lower().replace(" ", "_")
                if akey not in author_map:
                    author_map[akey] = AuthorNode(
                        author_id=akey, display_name=author_name, paper_count=1,
                    )
                else:
                    author_map[akey].paper_count += 1
                if edge_count < self.config.max_graph_edges:
                    edges.append({
                        "source": paper.work_id, "target": akey,
                        "type": "AUTHORED_BY",
                    })
                    edge_count += 1

            # Topic nodes + HAS_TOPIC edges
            for topic_name in paper.topics[:10]:
                tkey = topic_name.lower().replace(" ", "_")
                if tkey not in topic_map:
                    topic_map[tkey] = TopicNode(
                        topic_id=tkey, display_name=topic_name, paper_count=1,
                    )
                else:
                    topic_map[tkey].paper_count += 1
                if edge_count < self.config.max_graph_edges:
                    edges.append({
                        "source": paper.work_id, "target": tkey,
                        "type": "HAS_TOPIC",
                    })
                    edge_count += 1

            # Citation edges
            for ref in paper.referenced_works:
                if ref in paper_ids and edge_count < self.config.max_graph_edges:
                    edges.append({
                        "source": paper.work_id, "target": ref,
                        "type": "CITES",
                    })
                    edge_count += 1

        # Add non-paper nodes
        for a in author_map.values():
            nodes.append({
                "id": a.author_id, "type": "author",
                "display_name": a.display_name, "paper_count": a.paper_count,
            })
        for t in topic_map.values():
            nodes.append({
                "id": t.topic_id, "type": "topic",
                "display_name": t.display_name, "paper_count": t.paper_count,
            })

        snapshot_id = f"graph_{stable_hash({'corpus': corpus.corpus_id, 'nodes': len(nodes), 'edges': len(edges)}, 12)}"
        snapshot = GraphSnapshot(
            graph_snapshot_id=snapshot_id, corpus_id=corpus.corpus_id,
            nodes=nodes, edges=edges, parameters=parameters or {},
        )
        self.snapshots[snapshot_id] = snapshot
        self.artifacts.write_json("graph", f"{snapshot_id}.json", snapshot, "graph_snapshot",
                                  {"nodes": len(nodes), "edges": len(edges)})

        # Persist to MySQL if available
        if self._repo:
            try:
                self._repo.save_graph_snapshot(snapshot)
            except Exception:
                pass

        return snapshot

    # ── PageRank ─────────────────────────────────────────────

    def run_pagerank(self, snapshot: GraphSnapshot, iterations: int = 30,
                     damping: float = 0.85) -> Dict[str, float]:
        nodes_list = [node["id"] for node in snapshot.nodes]
        if not nodes_list:
            return {}
        outgoing: Dict[str, List[str]] = defaultdict(list)
        incoming: Dict[str, List[str]] = defaultdict(list)
        for edge in snapshot.edges:
            outgoing[edge["source"]].append(edge["target"])
            incoming[edge["target"]].append(edge["source"])
        rank = {node: 1.0 / len(nodes_list) for node in nodes_list}
        for _ in range(iterations):
            base = (1.0 - damping) / len(nodes_list)
            next_rank = {node: base for node in nodes_list}
            dangling = sum(rank[node] for node in nodes_list if not outgoing.get(node))
            for node in nodes_list:
                next_rank[node] += damping * dangling / len(nodes_list)
                for src in incoming.get(node, []):
                    next_rank[node] += damping * rank[src] / max(1, len(outgoing[src]))
            rank = next_rank
        return dict(sorted(rank.items(), key=lambda item: item[1], reverse=True))

    # ── Community detection (Louvain with fallback) ──────────

    def detect_communities(self, snapshot: GraphSnapshot) -> Dict[str, int]:
        """Louvain community detection; falls back to BFS connected components."""
        try:
            return self._louvain_communities(snapshot)
        except Exception:
            return self._bfs_communities(snapshot)

    def _louvain_communities(self, snapshot: GraphSnapshot) -> Dict[str, int]:
        """Louvain via python-louvain (community) package."""
        import community as community_louvain
        G = self._to_networkx(snapshot)
        partition = community_louvain.best_partition(G.to_undirected())
        return partition

    def _bfs_communities(self, snapshot: GraphSnapshot) -> Dict[str, int]:
        """BFS connected components fallback."""
        adjacency: Dict[str, set[str]] = defaultdict(set)
        for edge in snapshot.edges:
            adjacency[edge["source"]].add(edge["target"])
            adjacency[edge["target"]].add(edge["source"])
        community: Dict[str, int] = {}
        community_id = 0
        for node in [n["id"] for n in snapshot.nodes]:
            if node in community:
                continue
            queue = deque([node])
            community[node] = community_id
            while queue:
                cur = queue.popleft()
                for nxt in adjacency[cur]:
                    if nxt not in community:
                        community[nxt] = community_id
                        queue.append(nxt)
            community_id += 1
        return community

    def _to_networkx(self, snapshot: GraphSnapshot) -> Any:
        """Build a NetworkX DiGraph from a GraphSnapshot."""
        try:
            import networkx as nx
        except ImportError:
            raise ImportError("networkx is required for Louvain.  pip install networkx")
        G = nx.DiGraph()
        for node in snapshot.nodes:
            G.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
        for edge in snapshot.edges:
            G.add_edge(edge["source"], edge["target"], type=edge.get("type", "CITES"))
        return G

    # ── Bridge papers ────────────────────────────────────────

    def find_bridge_papers(self, snapshot: GraphSnapshot,
                           communities: Dict[str, int]) -> Dict[str, float]:
        scores: Dict[str, float] = defaultdict(float)
        for edge in snapshot.edges:
            src_comm = communities.get(edge["source"])
            dst_comm = communities.get(edge["target"])
            if src_comm is not None and dst_comm is not None and src_comm != dst_comm:
                scores[edge["source"]] += 1.0
                scores[edge["target"]] += 1.0
        return dict(sorted(scores.items(), key=lambda item: item[1], reverse=True))

    # ── Yearly trend & topic statistics ──────────────────────

    def compute_yearly_trend(self, corpus: Corpus) -> Dict[int, int]:
        counts = Counter(
            p.publication_year for p in corpus.papers if p.publication_year
        )
        return dict(sorted(counts.items()))

    def compute_topic_statistics(self, corpus: Corpus,
                                  top_k: int = 20) -> List[Dict[str, Any]]:
        counts = Counter(topic for paper in corpus.papers for topic in paper.topics)
        return [{"topic": topic, "count": count}
                for topic, count in counts.most_common(top_k)]

    # ── Key paper ranking ────────────────────────────────────

    def rank_key_papers(self, corpus: Corpus, snapshot: GraphSnapshot,
                        limit: Optional[int] = None) -> List[Dict[str, Any]]:
        limit = min(limit or self.config.max_key_papers, self.config.max_key_papers)
        pagerank = self.run_pagerank(snapshot)
        communities = self.detect_communities(snapshot)
        bridge = self.find_bridge_papers(snapshot, communities)
        max_cites = max([p.cited_by_count for p in corpus.papers] or [1])

        # Community representatives
        community_best: Dict[int, str] = {}
        for work_id, comm in communities.items():
            if work_id not in {p.work_id for p in corpus.papers}:
                continue
            current = community_best.get(comm)
            if current is None or pagerank.get(work_id, 0) > pagerank.get(current, 0):
                community_best[comm] = work_id
        representatives = set(community_best.values())

        ranked: List[Tuple[float, Paper, str]] = []
        for paper in corpus.papers:
            score = (
                0.45 * pagerank.get(paper.work_id, 0.0)
                + 0.30 * (paper.cited_by_count / max_cites)
                + 0.15 * (1.0 if paper.work_id in representatives else 0.0)
                + 0.10 * min(1.0, bridge.get(paper.work_id, 0.0) / 3.0)
            )
            role = "community_representative" if paper.work_id in representatives else "influential_paper"
            if bridge.get(paper.work_id, 0) > 0:
                role = "bridge_paper"
            if paper.publication_year and paper.publication_year >= 2022:
                role = "recent_representative"
            ranked.append((score, paper, role))

        result = [
            {
                "work_id": paper.work_id, "title": paper.title,
                "score": round(score, 6), "role": role,
                "pagerank": round(pagerank.get(paper.work_id, 0.0), 6),
                "community": communities.get(paper.work_id),
                "bridge_score": bridge.get(paper.work_id, 0.0),
                "cited_by_count": paper.cited_by_count,
                "publication_year": paper.publication_year,
            }
            for score, paper, role in sorted(ranked, key=lambda item: item[0], reverse=True)[:limit]
        ]

        # Store metrics
        algo_run_id = f"algo_{stable_hash({'snapshot': snapshot.graph_snapshot_id, 'ts': utc_now_iso()}, 12)}"
        self.metrics[snapshot.graph_snapshot_id] = {
            "pagerank": pagerank, "communities": communities, "bridge": bridge,
            "key_papers": result, "algo_run_id": algo_run_id,
        }
        self.artifacts.write_json("graph", f"{snapshot.graph_snapshot_id}_metrics.json",
                                  self.metrics[snapshot.graph_snapshot_id], "graph_metrics")

        # Persist algorithm run
        if self._repo:
            try:
                self._repo.save_graph_algorithm_run({
                    "algo_run_id": algo_run_id,
                    "graph_snapshot_id": snapshot.graph_snapshot_id,
                    "algorithm": "pagerank+louvain+bridge_v2",
                    "parameters": {"limit": limit},
                    "results": {"key_papers_count": len(result)},
                })
            except Exception:
                pass

        return result

    # ── Convenience ──────────────────────────────────────────

    def map_field_structure(self, corpus: Corpus) -> Dict[str, Any]:
        snapshot = self.build_graph_snapshot(corpus)
        key_papers = self.rank_key_papers(corpus, snapshot)
        yearly = self.compute_yearly_trend(corpus)
        topics = self.compute_topic_statistics(corpus)
        return {
            "snapshot_id": snapshot.graph_snapshot_id,
            "snapshot": snapshot,
            "yearly_trend": yearly,
            "topic_statistics": topics,
            "key_papers": key_papers,
            "node_count": len(snapshot.nodes),
            "edge_count": len(snapshot.edges),
            "communities_count": len(set(
                self.metrics.get(snapshot.graph_snapshot_id, {}).get("communities", {}).values()
            )),
        }

    # ── MCPResult builder ────────────────────────────────────

    def result(self, run_id: str, task_id: str, tool_call_id: str,
               result_type: str, raw_result: Any) -> MCPResult:
        summary: Dict[str, Any] = {}
        if isinstance(raw_result, dict):
            summary = {
                "snapshot_id": raw_result.get("snapshot_id", ""),
                "key_papers": len(raw_result.get("key_papers", [])),
                "node_count": raw_result.get("node_count", 0),
            }
        return MCPResult(
            tool_call_id=tool_call_id, analysis_run_id=run_id, task_id=task_id,
            provider="graph-analytics", status="completed", result_type=result_type,
            scope={"snapshot_id": summary.get("snapshot_id", "")},
            method={"name": "pagerank+louvain+bridge_v2", "version": "2.0"},
            summary=summary,
            preview=raw_result.get("key_papers", [])[:5] if isinstance(raw_result, dict) else [],
            provenance={"created_at": utc_now_iso(), "software_version": "research-agent-mvp-0.2"},
        )
