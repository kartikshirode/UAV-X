# Collector output on /uavx_eval/metrics. Not swarm traffic, and it never
# carries a SwarmPacket.
# Frozen in stage-1/architecture.md section 1b.

# Version of scenarios/run-record.schema.json this payload follows.
uint32 schema_version
string run_id
# Scenario file the run was launched from, relative to the repo root.
string scenario_path
# One UTF-8 JSON object matching scenarios/run-record.schema.json.
# Serialized rather than flattened because observations, handback and
# resources are nested objects.
string json_payload
# True on the last publication of a run. Earlier ones are partial.
bool final
