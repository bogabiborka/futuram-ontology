# sparql-llm — the FutuRaM chat webapp + MCP server over the Fuseki endpoints.
#
# One image, two entrypoints (compose runs it as two services with different CMDs):
#   * webapp : uvicorn sparql_llm.agent.main:app   (FastAPI chat UI, port 8000)
#   * mcp    : sparql-llm --http --port 8888        (MCP server)
#
# Teaching files (futuram_void.ttl / futuram_examples.ttl) + settings.json are
# baked in; .env is provided at runtime by compose (LLM model, OLLAMA_HOST).
# The Qdrant RAG index is populated by a one-shot `indexer` service before these
# start (see docker-compose.yml).
FROM python:3.12-slim

WORKDIR /app

# git: some sparql-llm[agent] deps build from source; curl: healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

# The agent extra pulls FastAPI/uvicorn/langchain; langchain-ollama gives the
# `ollama` provider that load_chat_model needs for the ollama/<model> string.
RUN pip install --no-cache-dir "sparql-llm[agent]" langchain-ollama

# sparql-llm's agent.main calls templates.TemplateResponse("index.html", {ctx})
# — the OLD Starlette signature. Starlette 1.x (pulled by latest FastAPI) requires
# TemplateResponse(request, name, ctx) and crashes the chat UI ("unhashable type:
# 'dict'"). Pin FastAPI/Starlette to the pre-1.x line where the old call works.
RUN pip install --no-cache-dir "fastapi==0.115.6" "starlette==0.41.3"

# Teaching vocab + examples + endpoint config (baked). The .docker.json variant
# advertises the browser-reachable localhost:47031 URL (the in-container agent
# rewrites it to host.docker.internal via patch 2 below) and the compose
# vectordb. ONE backend: the futuram datasource (real ELV fleet).
COPY sparql-llm/futuram_void.ttl          /app/sparql-llm/futuram_void.ttl
COPY sparql-llm/futuram_examples.ttl      /app/sparql-llm/futuram_examples.ttl
COPY sparql-llm/settings.docker.json      /app/sparql-llm/settings.json

# FutuRaM rebrand: replace the stock "Ask Expasy" intro page. The webapp serves
# index.html from /app/src/sparql_llm/agent/webapp (copied below from the package
# dir), so overwrite it in the PACKAGE dir first and the copy propagates it.
COPY sparql-llm/webapp/index.html \
     /usr/local/lib/python3.12/site-packages/sparql_llm/agent/webapp/index.html

# Two build-time patches (see patch_extract_retry.py):
#   1. extract_user_question retries on Ollama's intermittent async None (-> 500).
#   2. query_sparql rewrites localhost:4703x -> host.docker.internal:4703x so the
#      agent (in-container) can execute against the host-published Fuseki ports,
#      while the browser keeps the reachable localhost URL for the "Run query" link.
COPY sparql-llm/patch_extract_retry.py /tmp/patch_extract_retry.py
RUN python /tmp/patch_extract_retry.py

# Patch 3: relax the VoID query validator so it does NOT falsely reject a valid
# predicate when it cannot resolve the subject's type (property-path / subClassOf*
# typing, or a concrete subclass with no materialised partition). It was emitting
# "does not support the predicate ... can have the following predicates: ``" with
# an EMPTY list — a blind validator must not block. (patch_validate_void.py)
COPY sparql-llm/patch_validate_void.py /tmp/patch_validate_void.py
RUN python /tmp/patch_validate_void.py

ENV SETTINGS_FILEPATH=/app/sparql-llm/settings.json

# Profile-less backend selection: render_settings.sh writes settings.json from
# the BACKEND_* env vars at container START (futuram-shaped defaults), then
# exec's the CMD. So ONE chat / mcp / indexer image serves ANY backend — the
# compose profiles gate only the triplestores, and chat+mcp stay always-up.
COPY sparql-llm/render_settings.sh /usr/local/bin/render_settings.sh
RUN chmod +x /usr/local/bin/render_settings.sh
ENTRYPOINT ["/usr/local/bin/render_settings.sh"]

# sparql-llm caches endpoints_metadata.json to a RELATIVE  data/  dir (CWD-based);
# create it writable so the indexer/agent can write there from WORKDIR /app.
RUN mkdir -p /app/data && chmod 0777 /app/data

# The chat webapp serves its UI via Jinja + StaticFiles from the RELATIVE path
# "src/sparql_llm/agent/webapp" (a dev-tree path). In the installed package that
# dir is under site-packages with no src/ prefix, so "/" 404s and StaticFiles
# crashes. Copy the installed webapp to that relative path (resolved from
# WORKDIR /app). Plain shell cp (Python import prints HF/onnx warnings).
RUN set -eux; \
    PKG="/usr/local/lib/python3.12/site-packages/sparql_llm/agent/webapp"; \
    mkdir -p /app/src/sparql_llm/agent; \
    cp -r "$PKG" /app/src/sparql_llm/agent/webapp; \
    ls /app/src/sparql_llm/agent/webapp/assets

# webapp by default; the mcp service overrides CMD in compose.
EXPOSE 8000
CMD ["uvicorn", "sparql_llm.agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
