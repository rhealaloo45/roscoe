# Quickstart — build your first agent

An A-to-Z walkthrough: from nothing installed to a working agent built entirely
in the visual editor. Takes about 15 minutes.

Already comfortable with the SDK and just want the reference? See
[`docs.md`](docs.md) instead — this file is the guided first run.

---

## What you're going to build

A small 3-step **Daily Email Digest** agent:

```
Fetch emails
      │
      ▼
  Classify
      │
      ▼
 Summarize
```

1. **Action** — connects to Gmail and pulls the most recent inbox messages
   (sender, subject, snippet)
2. **Prompt** — asks the AI model to classify each email as Urgent, Action
   Needed, or FYI
3. **Prompt** — turns that classification into a short digest a manager can
   read in ten seconds

Three nodes, two of the four node types roscoe supports (`connector_action`
and `llm_step` — `condition` and `agent_step` are covered in `docs.md` once
you're past the basics). Small enough to build in a few minutes, real enough
to show how a connector call and an AI step chain together — the same
pattern behind any "read something, triage it, brief someone" agent,
whether the source is an inbox, a ticket queue, or a support mailbox.

---

## Step 1 — Set up a Python environment and install roscoe

Open a terminal (Command Prompt on Windows, Terminal on Mac), pick a folder to
work in, then:

```bash
cd Desktop
python3 -m venv roscoe-env
```

Activate it:

```bash
# Mac / Linux:
source roscoe-env/bin/activate

# Windows:
roscoe-env\Scripts\activate
```

Your prompt should now start with `(roscoe-env)` — that's how you know it's
active. **Run the activate command again any time you reopen a terminal for
this project.**

Install roscoe:

```bash
pip install roscoe
```

---

## Step 2 — Get an API key for an AI model

Pick one:

- **NVIDIA NIM** (free tier, recommended for this tutorial) — sign up at
  [build.nvidia.com](https://build.nvidia.com), grab an API key. No credit
  card needed for the free tier.
- **OpenAI** — [platform.openai.com/api-keys](https://platform.openai.com/api-keys),
  small cost per use.
- **Ollama** — [ollama.com](https://ollama.com), completely free, runs
  locally, no key at all. Run `ollama pull llama3.1` after installing.

This guide shows NVIDIA in the screenshots; swap in whichever you picked —
only the Setup tab's Model fields change, nothing else in this tutorial does.

---

## Step 3 — Scaffold your project

```bash
roscoe init-nc my-first-agent
cd my-first-agent
cp .env.example .env
```

Open `.env` in any text editor and add your key:

```
NVIDIA_API_KEY=nvapi-your-actual-key-here
```

(Swap the variable name if you picked OpenAI instead — `OPENAI_API_KEY=...`.)

---

## Step 4 — Connect Gmail (one-time OAuth setup)

This example reads your inbox, so it needs Gmail credentials. This is the
only fiddly one-time part — everything after this step is just the builder.

1. Go to [console.cloud.google.com](https://console.cloud.google.com), create
   a project (or pick an existing one).
2. **APIs & Services → Library** — enable the **Gmail API**.
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID** —
   application type **Desktop app**. Download or copy the **Client ID** and
   **Client Secret**.
4. Add both to `.env`:

   ```
   GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your-client-secret
   ```

5. Run roscoe's built-in helper to mint the last credential — it opens a
   browser, you approve access, and it writes the result straight into `.env`:

   ```bash
   roscoe google-auth
   ```

   This appends `GOOGLE_REFRESH_TOKEN=...` to `.env` automatically. Nothing
   to copy-paste by hand.

If your Google account has 2-step verification or an org policy blocking
"unverified apps," Google will show a warning screen during consent — click
**Advanced → Go to (your app name)** to proceed; this is expected for an app
you just created and haven't submitted for verification.

---

## Step 5 — Open the builder

```bash
roscoe build
```

Opens `http://localhost:8099` in your browser automatically. Two tabs at the
top: **Flow** and **Setup**. Keep this terminal open — closing it shuts the
builder down.

If you're starting from a totally blank canvas, skip to Step 6. If
`roscoe init-nc` left a sample workflow on the canvas (it does, by default —
a small example of its own), click each node and use **Delete node** in the
side panel until the canvas is empty, so you're building the example below
from scratch.

---

## Step 6 — Set the model

Click the **Setup** tab. Under **Model**, fill in:

- **Provider**: `nvidia`
- **Model name**: `openai/gpt-oss-120b`
- **API key**: `${NVIDIA_API_KEY}`

`${NVIDIA_API_KEY}` is not a typo — it tells roscoe to read the real value
from `.env`, so the key itself never gets written into a config file.

![Setup tab with the model filled in and the gmail connector added](quickstart_images/01-setup-model-and-connector.png)

---

## Step 7 — Add a connector

Still on the **Setup** tab, under **Connectors**, click **+ connector** and
fill in:

- **Name**: `gmail`
- **Type**: `google_workspace`
- **Setting**: key `client_id`, value `${GOOGLE_CLIENT_ID}`
- **Setting**: key `client_secret`, value `${GOOGLE_CLIENT_SECRET}`
- **Setting**: key `refresh_token`, value `${GOOGLE_REFRESH_TOKEN}`

All three come from Step 4 and are already sitting in `.env` — same
`${VAR}` pattern as the model's API key, so nothing sensitive ends up in
`workflow.yaml` or `agent_config.yaml`. (Screenshot above shows this filled
in, alongside the model.)

Click **Save setup**.

---

## Step 8 — Build the flow

Click back to the **Flow** tab.

**Node 1 — the Action.** Click **Action** in the palette, then fill in the
panel on the right:

- **Name**: `fetch_emails`
- **Connector**: `gmail`
- **Method**: `read_emails`
- **Inputs**: click **+ input** → name `max_results`, value `10`
- **Save result as**: `inbox`

**Node 2 — the first Prompt.** Click **Prompt**, then:

- **Name**: `classify`
- **Prompt**: `Classify each of these emails as Urgent, Action Needed, or FYI. Return a short bullet list with sender, subject, and category:\n\n{{ inbox }}`
- **Save result as**: `classified`

**Node 3 — the second Prompt.** Click **Prompt** again, then:

- **Name**: `summarize`
- **Prompt**: `Write a short daily email digest (3-5 lines, no headers) for a busy manager, grouped by urgency, from this classification:\n\n{{ classified }}`
- **Save result as**: `answer`

![The three nodes connected on the canvas: fetch_emails, classify, summarize](quickstart_images/02-flow-three-nodes.png)

Connect them: drag from `fetch_emails`'s output dot to `classify`, then from
`classify`'s output dot to `summarize` — or just set "Then go to" in each
node's panel, which does the same thing.

At the top toolbar:
- **Entry**: `fetch_emails`
- **Workflow output**: `{{ answer }}`

Click **Save workflow.yaml**.

---

## Step 9 — Check it before running it

```bash
roscoe validate
```

```
roscoe validate — agent_config.yaml
  3 node(s), entry='fetch_emails'
  connectors: gmail

  No problems found.
```

If it flags anything, it names the exact node and field — fix it in the
builder, save, and run `roscoe validate` again.

---

## Step 10 — Run it

```bash
roscoe run
```

A chat page opens. Type anything and press Enter — the message itself
doesn't matter here, since this workflow doesn't read it; it just triggers
the run. A few seconds later you'll see a digest of your real inbox, e.g.:

> **Urgent:** Payroll run failed (bank@acme-payroll.com).
> **Action Needed:** Q3 budget review (priya@acme.com).
> **FYI:** Office closed Monday (hr@acme.com).

![The running agent's final answer in the browser chat](quickstart_images/03-run-result.png)

**You just built and ran a 3-step agent.**

---

## Other things `roscoe` can do, once you're running for real

These aren't part of building the agent itself, but they're the commands
you'll reach for next as this stops being a toy example:

```bash
roscoe monitor --path logs/audit.jsonl   # dashboard: cost, latency, error rate
roscoe eval --dataset evals/test_cases.json --config agent_config.yaml   # score it against test cases
roscoe prices                            # edit what each model costs, for accurate cost tracking
roscoe graph                             # see the workflow as a flowchart
roscoe run --terminal                    # chat in the terminal instead of the browser
```

Every run already writes to `logs/audit.jsonl` and tracks cost automatically —
`roscoe monitor` and `roscoe prices` are just how you look at and tune that
after the fact. `docs.md` covers all of these in depth, along with evals,
alerts, and exporters.

---

## What just happened

- `agent_config.yaml` holds *how* your agent thinks — model, retries, rate
  limiting, cost tracking (already sensibly defaulted, you didn't touch them).
- `workflow.yaml` holds *what* your agent does — three steps here. More
  connectors, branching (`condition` nodes), and multi-agent hand-offs
  (`agent_step` nodes) all get added the same way: a palette button, a panel,
  Save.
- Nothing here is locked to NVIDIA, or to Gmail. Change the model in Setup,
  swap the connector and prompts in Flow, and it's a different agent — the
  same three steps (fetch data, classify, summarise) work for a support
  mailbox, a ticket queue, or a Slack channel just as well.

---

## Where to go next

- **[`docs.md`](docs.md)** — the full reference: every node type, every
  connector (Gmail, GitHub, Notion, databases, TickTick, and more), human
  approval gates, memory, monitoring, evals.
- **`custom_ui_example.py`** — once your agent does something real, this is a
  working starting point for a front end nicer than the default chat box.
- **Written code instead?** `roscoe init my-agent` scaffolds a project built
  around Python tool functions instead of the visual editor — same SDK,
  different way of building.
