#!/usr/bin/env python3
"""Check a ROS graph against the tx/rx seam allowlist.

This is the half of scripts/check_seam.sh that reads a graph. It lives in its
own file so the fixtures can call it directly and read what it said, rather than
running the whole shell wrapper and reading only an exit code.

Round 4 finding 4 listed what the old version got wrong, and every one of them
is the same shape: a check that looked strict and had a way past it.

  - one manifest for every scenario, demanding W4 processes during W3
  - the static pass never rerun after W4 added the role managers
  - a topic that was not /uavx/, not ground truth and not a PX4 namespace fell
    through every branch and was allowed, so /swarm/broadcast was invisible
  - message types were thrown away at parse time, so the rule banning
    SwarmPacket outside tx/rx could not be enforced at all
  - outside processes matched by substring, so /uav_2/link_layer_helper
    inherited the link layer's right to read simulator ground truth

So this one works from an allowlist and rejects everything not on it, rather
than from a list of known bypasses.

    python3 scripts/seam_graph.py --scenario relay_required --snapshot g.json
    python3 scripts/seam_graph.py --scenario mission_integrated --live

Exit 0 clean, 1 violations, 2 the check could not be run.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFESTS = REPO / "scripts" / "seam_manifests.json"

GROUND_TRUTH = re.compile(r"/gazebo/|model_states|ModelStates|GetModelState")
PARAM_SVC = re.compile(
    r"/(describe_parameters|get_parameter_types|get_parameters|list_parameters"
    r"|set_parameters|set_parameters_atomically|get_type_description)$")
KINDS = ("publishers", "subscribers", "services", "actions")


def die(msg: str) -> None:
    print(f"  CANNOT CHECK  {msg}", file=sys.stderr)
    sys.exit(2)


def load_manifest(scenario: str) -> dict:
    if not MANIFESTS.is_file():
        die(f"{MANIFESTS} is missing")
    m = json.loads(MANIFESTS.read_text(encoding="utf-8"))
    if scenario not in m["scenarios"]:
        die(f"no seam manifest for scenario {scenario!r}. "
            f"Known: {', '.join(sorted(m['scenarios']))}")
    s = m["scenarios"][scenario]
    expected = [f"/{v}/{p}" for v in m["vehicles"] for p in s["per_vehicle"]]
    expected += s["extra"]
    return {
        "vehicles": m["vehicles"],
        "outside": set(m["outside_processes"]),
        "packet_type": m["swarm_packet_type"],
        "infra_pub": set(m["ros_infrastructure"]["publish"]),
        "infra_sub": set(m["ros_infrastructure"]["subscribe"]),
        "expected": set(expected),
        "week": s["week"],
    }


def read_snapshot(path: Path) -> dict:
    if not path.is_file():
        die(f"no graph snapshot at {path}. The scenario runner captures one "
            f"while the scenario is up.")
    try:
        g = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"{path} is not valid JSON: {exc}")
    if not isinstance(g, dict) or not g:
        die(f"{path} holds no nodes")
    for node, ep in g.items():
        for kind in KINDS:
            for e in ep.get(kind, []):
                if not isinstance(e, dict) or "topic" not in e or "type" not in e:
                    die(f"{path}: {node} lists {kind} without a message type. "
                        f"Types are what rule 6 is enforced from; a snapshot "
                        f"without them cannot be checked and must not pass.")
    return g


def read_live() -> dict:
    try:
        nodes = subprocess.run(["ros2", "node", "list"], capture_output=True,
                               text=True, timeout=60).stdout.split()
    except (OSError, subprocess.SubprocessError) as exc:
        die(f"ros2 node list failed: {exc}")
    if not nodes:
        die("no ROS nodes running. The live pass needs a scenario up.")
    out = {}
    for n in nodes:
        info = subprocess.run(["ros2", "node", "info", n], capture_output=True,
                              text=True, timeout=60).stdout
        cur = None
        d = {k: [] for k in KINDS}
        for line in info.splitlines():
            t = line.strip()
            low = t.lower()
            if low.startswith("subscribers:"):
                cur = "subscribers"; continue
            if low.startswith("publishers:"):
                cur = "publishers"; continue
            if low.startswith(("service servers:", "service clients:")):
                cur = "services"; continue
            if low.startswith(("action servers:", "action clients:")):
                cur = "actions"; continue
            if cur and t.startswith("/"):
                topic, _, typ = t.rpartition(":")
                d[cur].append({"topic": (topic or t).strip(),
                               "type": typ.strip()})
        out[n] = d
    return out


def check(graph: dict, man: dict) -> list:
    v: list[str] = []
    vehicles = man["vehicles"]
    packet = man["packet_type"]

    # 1. The process list, both directions. A process that should be there and
    #    is not means the scenario did not actually run what the gate thinks.
    #    A process that is there and should not be is an unaccounted-for member
    #    of the swarm, which is where a bypass hides.
    present = set(graph)
    for want in sorted(man["expected"] - present):
        v.append(f"expected process {want} is absent from the graph")
    for extra in sorted(present - man["expected"] - man["outside"]):
        v.append(f"{extra} is in the graph and in neither this scenario's "
                 f"manifest nor the outside-process list")

    for node in sorted(graph):
        ep = graph[node]

        if node in man["outside"]:
            v += check_outside(node, ep, vehicles, packet)
            continue
        if node not in man["expected"]:
            continue  # already reported above; do not double-count its topics

        own = node.strip("/").split("/")[0]
        for kind in KINDS:
            for e in ep.get(kind, []):
                v += check_endpoint(node, own, kind, e["topic"], e["type"],
                                    man, packet)
    return v


def check_endpoint(node, own, kind, topic, typ, man, packet) -> list:
    """One endpoint of one swarm process, against the section 1 allowlist.

    Written as allow-then-reject on purpose. The old version was a chain of
    elif branches testing for known bad shapes, and anything matching none of
    them reached the end of the chain and was permitted.
    """
    tx, rx = f"/uavx/{own}/tx", f"/uavx/{own}/rx"

    # Ground truth first, so it cannot be laundered through a vehicle namespace.
    if GROUND_TRUTH.search(topic):
        return [f"{node} reads simulator ground truth {topic}; only the link "
                f"layer and uavx_eval may"]

    if kind in ("services", "actions"):
        if kind == "services" and PARAM_SVC.search(topic) and topic.startswith(node + "/"):
            return []
        return [f"{node} exposes {kind[:-1]} {topic}; swarm traffic goes "
                f"through tx and rx only"]

    # The allowlist decides first, and its answer is the more useful message.
    # Checking the message type ahead of it reported a node reading a
    # neighbour's rx as "SwarmPacket on the wrong topic", which is true and
    # tells whoever reads the failure much less than "that endpoint belongs to
    # uav_4".
    if kind == "publishers":
        allowed = topic == tx or topic in man["infra_pub"]
        wrong_way = topic == rx
        direction = f"{node} publishes to {topic}; nodes publish to tx only"
    else:
        allowed = topic == rx or topic in man["infra_sub"]
        wrong_way = topic == tx
        direction = f"{node} subscribes to {topic}; nodes subscribe to rx only"

    if not allowed:
        if wrong_way:
            return [direction]
        if topic.startswith("/uavx/"):
            parts = topic.strip("/").split("/")
            if len(parts) < 3:
                return [f"{node} holds shared endpoint {topic}; swarm traffic "
                        f"is per node only"]
            return [f"{node} holds {topic}, which belongs to {parts[1]}"]
        if topic.startswith(f"/{own}/"):
            allowed = True  # its own PX4 namespace, explicitly allowed
        else:
            m = re.match(r"^/(uav_\d+|gcs)/", topic)
            if m:
                return [f"{node} touches {topic}, the namespace of {m.group(1)}"]
            return [f"{node} holds {topic}, which is on no allowlist row. Every "
                    f"swarm endpoint is either its own tx, its own rx, its own "
                    f"PX4 namespace or ROS infrastructure."]

    # Allowed by topic. Rule 6 still applies: swarm payload only crosses the
    # seam, so a legal topic carrying SwarmPacket is a side channel.
    if typ == packet and topic not in (tx, rx):
        return [f"{node} carries {packet} on {topic}, which is not its tx or rx"]
    return []


def check_outside(node, ep, vehicles, packet) -> list:
    """The three processes that sit outside the swarm still have limits.

    The link layer is the radio and the collector is the observer. Neither may
    become a way for the swarm to talk, and the runner may not touch swarm
    traffic at all.
    """
    v = []
    tx = {f"/uavx/{x}/tx" for x in vehicles + ["gcs"]}
    rx = {f"/uavx/{x}/rx" for x in vehicles + ["gcs"]}
    pubs = [e["topic"] for e in ep.get("publishers", [])]
    subs = [e["topic"] for e in ep.get("subscribers", [])]

    if node == "/link_layer":
        for t in pubs:
            if t in tx:
                v.append(f"{node} publishes to {t}. The link layer delivers to "
                         f"rx; publishing to tx would let it invent traffic.")
        for t in subs:
            if t in rx:
                v.append(f"{node} subscribes to {t}. Reading its own output is "
                         f"a loop, not a radio.")
    elif node == "/metrics_collector":
        for t in pubs:
            if t in tx or t in rx:
                v.append(f"{node} publishes to {t}. The observer observes.")
    elif node == "/scenario_runner":
        for t in pubs + subs:
            if t in tx or t in rx:
                v.append(f"{node} holds {t}. The runner injects failures by "
                         f"stopping processes, never by sending swarm traffic.")
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--snapshot")
    src.add_argument("--live", action="store_true")
    a = ap.parse_args()

    man = load_manifest(a.scenario)
    graph = read_live() if a.live else read_snapshot(Path(a.snapshot))

    violations = check(graph, man)
    if violations:
        for x in violations:
            print(f"  VIOLATION  {x}")
        print(f"\n{len(violations)} seam violation(s) in {a.scenario}. "
              f"The communication resilience claim does not hold.")
        return 1

    print(f"  ok         {len(graph)} nodes in {a.scenario} (week "
          f"{man['week']}) respect the seam")
    return 0


if __name__ == "__main__":
    sys.exit(main())
