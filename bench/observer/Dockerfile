# The bench observer UI (Next.js) — AND the host for the run_bench.py launches its
# API routes spawn. So this image carries BOTH the Node runtime (to serve the UI)
# and uv + Python (to run the benchmark). The repo is mounted at /repo at runtime
# (see the compose file), so the data, testcases, experiments and run_bench all
# come from the live checkout — no data baked into the image.
FROM node:22-bookworm-slim

# uv (standalone binary) for `uv run bench/run_bench.py …`; git + ca-certs for uv's
# Python download. No system Python needed — uv manages its own toolchain.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates git \
 && rm -rf /var/lib/apt/lists/* \
 && curl -LsSf https://astral.sh/uv/install.sh | sh \
 && ln -s /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /repo/bench/observer

# Install JS deps against the committed package.json/lock. node_modules lives in
# the image (a named volume in compose shadows the mounted host node_modules so a
# macOS host install can't leak in). Copy only the manifests first for layer cache.
COPY package.json package-lock.json* ./
RUN npm install

# The app code + the whole repo are MOUNTED at runtime (compose), not copied, so
# edits and data show up live and run_bench sees the real checkout.

ENV BENCH_REPO=/repo \
    PORT=3737 \
    HOSTNAME=0.0.0.0

EXPOSE 3737

# Dev mode on purpose: the observer is a dev/inspection tool; dev mode avoids the
# prod-build/.next fragility and serves the mounted source live.
CMD ["npm", "run", "dev"]
