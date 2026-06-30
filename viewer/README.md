# Results viewer: static, published benchmark browser

A self-contained single-page app (Vite + React) that bakes one benchmark
experiment into a single `data.json` and serves it as a static site. The baked
data covers the experiment's competency questions, ground-truth answers, model
responses, and full reasoning transcripts. This is what's published at
**https://bogabiborka.github.io/futuram-ontology/**.

It is read-only and standalone. Unlike the live observer UI (`bench/observer`,
which drives runs against the stack), the viewer needs no Fuseki, no MCP server,
and no model, just the baked `data.json`. It's how you share a finished
experiment with someone who won't run the stack.

## How it works

```
bench/experiments/<name>/runs/<latest>/   --(bundle_data.py)-->  viewer/public/data.json   --(vite build)-->  static site
```

`bundle_data.py` picks an experiment, finds its latest run, and flattens every
`(case, backend)` transcript into one JSON the app loads at startup.

## Build & preview locally

From the repo root:

```sh
python viewer/bundle_data.py            # bundle the default experiment (all-in-1)
python viewer/bundle_data.py nightly-2  # ...or a specific experiment by name

cd viewer
npm ci
npm run dev                             # http://localhost:5173
```

`npm run build` produces the static `dist/`; `npm run preview` serves that build.
`npm run bundle` is a shortcut for `python3 bundle_data.py`.

The published default experiment is `all-in-1` (the one kept tracked under
`bench/experiments/all-in-1/`; other experiment outputs are gitignored). Override
it with a name on the CLI or via the workflow input below.

## Publishing (GitHub Pages)

`.github/workflows/deploy-viewer.yml` builds and deploys to GitHub Pages:

- it runs automatically on a push to `main` that touches `bench/experiments/**`,
  `viewer/**`, or the workflow file;
- it also runs on demand via *Actions, Deploy Viewer, Run workflow*, where the
  `experiment` input selects which experiment to publish (default: latest by run
  timestamp).

Enable it once under *Settings, Pages, Source: GitHub Actions*.

## Layout

| path | what |
|---|---|
| `bundle_data.py` | flattens one experiment's latest run into `public/data.json` |
| `public/data.json` | the baked experiment data the app loads (regenerated, not hand-edited) |
| `src/` | the React app (Vite + Tailwind) |
| `index.html`, `vite.config.js`, `package.json` | the Vite project |
| `dist/` | the built static site (output of `npm run build`) |
