# FutuRaM Fuseki image — loads MOUNTED TTL into a PERSISTENT TDB2 store at
# CONTAINER STARTUP (not image build), so new data needs only a restart, never
# an image rebuild.
#
#   * TTL source  : mounted read-only at /staging/{composition,query}
#                   (compose binds fuseki/<instance>/data there).
#   * TDB2 store  : a named volume at /fuseki-base/databases (persists across
#                   restarts; loaded once, reused after).
#   * On startup  : entrypoint loads each dataset IF its store is empty (or
#                   RELOAD=1 is set), then serves. Drop new TTL + `restart` to
#                   pick it up (RELOAD=1 to force a fresh load).
#
# secoresearch/fuseki ships both the Fuseki server AND the Jena CLI tools
# (tdb2.tdbloader), so one stage suffices.
FROM secoresearch/fuseki:latest

# Alpine base; bash + the TDB2 bulk loader (/jena/bin/tdb2.xloader) already ship.
USER root

COPY fuseki/config.ttl       /fuseki-base/config.ttl
COPY fuseki/entrypoint.sh     /entrypoint.sh
RUN chmod +x /entrypoint.sh && chmod -R 0777 /fuseki-base

# RELOAD=1 forces a re-load of the mounted TTL even if the store is non-empty.
ENV RELOAD=0
ENTRYPOINT ["/entrypoint.sh"]
