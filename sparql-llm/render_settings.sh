#!/bin/sh
# render_settings.sh — generate settings.json from environment, then exec "$@".
#
# Makes the chat / MCP / indexer PROFILE-LESS containers whose BACKENDS are
# configured by env, not by a per-profile settings file. The chat lists every
# configured backend (settings.endpoints) and a UI picker selects which ONE the
# agent queries per request — each backend is INDEPENDENT (its own endpoint +
# VoID); there is no union and no restart to switch.
#
# Inputs:
#   BACKENDS        '|'-separated backend specs, each a ';'-separated tuple
#                     id,label,endpoint_url,void_file[,description]
#                   Default = the one committed Fuseki backend (futuram).
#   DEFAULT_BACKEND id (or endpoint_url / label) of the backend selected first.
#                   Default = futuram.
#   EXAMPLES_FILE   shared example queries (default futuram_examples.ttl).
#   VECTORDB_URL    Qdrant URL (default http://vectordb:6333).
#   APP_NAME        chat title.
#
#   Back-compat single-backend override: if BACKEND_SPARQL_URL is set, it REPLACES
#   the registry with that one backend (BACKEND_VOID_FILE/LABEL/DESC as before).
#
# The localhost->host.docker.internal rewrite at query time is unchanged
# (patch_extract_retry.py / FUTURAM_FUSEKI_HOST), so each localhost:PORT URL here
# still resolves to the host-published port from inside the container.
set -eu

OUT="${SETTINGS_FILEPATH:-/app/sparql-llm/settings.json}"

# The default registry: the one committed Fuseki backend (the futuram datasource).
# id;label;endpoint_url;void_file;description
DEFAULT_BACKENDS="futuram;FutuRaM (ELV fleet);http://localhost:47031/query/sparql;/app/sparql-llm/futuram_void.ttl;Real end-of-life-vehicle fleet data across six drivetrains (elvBEV, elvDiesel, elvHEV, elvPHEV, elvPetrol, elvOther), 1980-2050, over the flat fq: query vocabulary."

export BACKENDS="${BACKENDS:-$DEFAULT_BACKENDS}"
export DEFAULT_BACKEND="${DEFAULT_BACKEND:-futuram}"
export EXAMPLES_FILE="${EXAMPLES_FILE:-/app/sparql-llm/futuram_examples.ttl}"
export VECTORDB_URL="${VECTORDB_URL:-http://vectordb:6333}"
export APP_NAME="${APP_NAME:-FutuRaM Chat}"

# Back-compat: a single-backend override collapses the registry to one entry.
if [ -n "${BACKEND_SPARQL_URL:-}" ]; then
  _bl="${BACKEND_LABEL:-FutuRaM}"
  _bv="${BACKEND_VOID_FILE:-/app/sparql-llm/futuram_void.ttl}"
  _bd="${BACKEND_DESC:-FutuRaM composition over the flat fq: query vocabulary.}"
  export BACKENDS="single;${_bl};${BACKEND_SPARQL_URL};${_bv};${_bd}"
  export DEFAULT_BACKEND="single"
fi

# Build settings.json with python (escapes quotes/newlines correctly).
SETTINGS_OUT="$OUT" \
python - <<'PY'
import json, os

EXAMPLES = os.environ["EXAMPLES_FILE"]
endpoints = []
for spec in os.environ["BACKENDS"].split("|"):
    spec = spec.strip()
    if not spec:
        continue
    parts = spec.split(";")
    # id;label;endpoint_url;void_file[;description]
    bid, label, url, void = (parts + ["", "", "", ""])[:4]
    desc = parts[4] if len(parts) > 4 else ""
    endpoints.append({
        "id": bid,
        "endpoint_url": url,
        "void_file": void,
        "examples_file": EXAMPLES,
        "label": label,
        "description": desc,
        "homepage_url": "https://www.purl.org/futuram",
    })

settings = {
    "app_name": os.environ["APP_NAME"],
    "app_org": "FutuRaM",
    "app_topics": "the material, component and element composition of products "
                  "via the flat fq: query vocabulary",
    "example_questions": [
        "How much copper is in elvBEV?",
        "Which elements does elvBEV contain, and in what amounts?",
        "What does V0301030105 contain at the Component level?",
        "List all classes that contain Copper.",
        "How much of each element is in the elvElectricMotor?",
    ],
    "vectordb_url": os.environ["VECTORDB_URL"],
    "default_backend": os.environ["DEFAULT_BACKEND"],
    "endpoints": endpoints,
}
with open(os.environ["SETTINGS_OUT"], "w") as f:
    json.dump(settings, f, indent=2)
print(f"[render_settings] {os.environ['SETTINGS_OUT']} -> "
      f"{len(endpoints)} backend(s): "
      f"{', '.join(e['id'] for e in endpoints)} "
      f"(default {settings['default_backend']})", flush=True)
PY

exec "$@"
