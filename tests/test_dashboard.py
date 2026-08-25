"""Tests for feltstate.dashboard — the read-only window's payload builders."""

from __future__ import annotations

import json

from feltstate.dashboard import graph_payload, node_payload, state_payload
from feltstate.memory.canon import Canon
from feltstate.memory.keyweb import SharedKeyJudge, digest_canon, imprint_into


def _seeded_canon(tmp_path):
    c = Canon(tmp_path / "canon.jsonl")
    a = c.add("ash", "the rent went up", intensity=0.9)
    b = c.add("ash", "dispute opened with the landlord", intensity=0.9)
    d = c.add("ash", "bought a kettle", intensity=0.6)
    imprint_into(c, a["id"], ["rent", "money"])
    imprint_into(c, b["id"], ["dispute", "money"])
    imprint_into(c, d["id"], ["kettle"])
    digest_canon(c, [b["id"]], judge=SharedKeyJudge(min_shared=1))
    return c, a, b, d


def test_graph_payload_nodes_edges_and_why(tmp_path):
    c, a, b, d = _seeded_canon(tmp_path)
    g = graph_payload(c.view(include_archived=True))
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {a["id"], b["id"], d["id"]}  # keyed facts only, all of them
    assert g["edge_count"] == 1
    edge = g["edges"][0]
    assert {edge["from"], edge["to"]} == {a["id"], b["id"]}
    assert "shared keys" in edge["title"]  # the judge's why rides the edge


def test_node_payload_walks_both_directions(tmp_path):
    c, a, b, d = _seeded_canon(tmp_path)
    rows = c.view(include_archived=True)
    # a's edge was written by b's digest pass — reverse direction must still show
    card = node_payload(rows, a["id"])
    assert card["brief"]["id"] == a["id"]
    assert [k["cid"] for k in card["kin"]] == [b["id"]]
    assert card["kin"][0]["why"]
    lonely = node_payload(rows, d["id"])
    assert lonely["kin"] == []
    assert node_payload(rows, "nope") == {"brief": None, "kin": []}


def test_state_payload_reads_or_declines(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"mood": {"valence": 0.4}}), encoding="utf-8")
    assert state_payload(str(p))["state"]["mood"]["valence"] == 0.4
    assert state_payload(str(tmp_path / "missing.json")) == {"state": None}
    assert state_payload(None) == {"state": None}
