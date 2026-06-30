# Overlay builder: ROBOT (for the ChEBI STAR module) + a copy of the static
# Metal-Wheel TTLs. Run as a one-shot `chebi-extract` compose service that
# writes the overlay into the futuram /query staging dir BEFORE Fuseki loads.
#
# Reads ONLY ontology/sources/ (mounted at /repo/ontology/sources) — no legacy
# dependency. Output goes to the mounted query dir.
FROM eclipse-temurin:21-jre

# ROBOT: a single fat jar + a wrapper script. Pin a known release.
ARG ROBOT_VERSION=1.9.7
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gzip ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -L -o /usr/local/bin/robot.jar \
        "https://github.com/ontodev/robot/releases/download/v${ROBOT_VERSION}/robot.jar" \
    && curl -L -o /usr/local/bin/robot \
        "https://raw.githubusercontent.com/ontodev/robot/v${ROBOT_VERSION}/bin/robot" \
    && chmod +x /usr/local/bin/robot

WORKDIR /repo
# The extract script is bind-mounted with the repo at runtime; default CMD runs it.
CMD ["bash", "scripts/extract_chebi_module.sh"]
