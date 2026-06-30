# Image for the optional EMMO/CEON/ChEBI consistency check.
# obolibrary/robot already ships ROBOT (robot.jar + wrapper on PATH); we add uv
# so scripts/robot_consistency.py can run with its inline rdflib dependency.
FROM obolibrary/robot:latest

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:${PATH}"
# uv resolves Python + rdflib per the script's PEP-723 header at run time.

WORKDIR /work
