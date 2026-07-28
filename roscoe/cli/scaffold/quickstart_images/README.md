# Screenshots needed here

`quickstart.md` references three images by these exact filenames. Drop them in
here (same folder) and they'll show up in the tutorial automatically.

1. **`01-setup-model-and-connector.png`** — the Setup tab, scrolled to show
   both the Model section (provider `nvidia`, model `openai/gpt-oss-120b`, API
   key `${NVIDIA_API_KEY}`) and the Connectors section (`gmail` /
   `google_workspace` / `client_id`, `client_secret`, `refresh_token` all set
   to `${...}` placeholders) in the same shot.

2. **`02-flow-three-nodes.png`** — the Flow tab canvas showing all three
   connected nodes: `fetch_emails` → `classify` → `summarize`. Zoom/pan so all
   three are visible with their connecting lines.

3. **`03-run-result.png`** — the browser chat opened by `roscoe run`, showing
   a completed answer (a short digest grouped by Urgent/Action Needed/FYI).

This exact example is verified working end-to-end (a real connector call
against Gmail's API — `read_emails` now fetches subject/sender/snippet per
message, not just message ids — chained into two LLM classify/summarise
steps). A real run against a realistic inbox produced:

> **Urgent:** Payroll run failed (bank@acme-payroll.com).
> **Action Needed:** Q3 budget review (priya@acme.com).
> **FYI:** Office closed Monday (hr@acme.com).

Delete this README once the three images are in place — it's not referenced
by `quickstart.md` itself.
