# DESIGN

> Copy this file to `DESIGN.md` and answer it **before you write code**. Revise it as you
> learn, but keep the first version in git history. A stranger grades it (`G-DESIGN`), so
> write for someone who has never seen your code. The five headings are fixed; keep them.

## Components

What are the moving parts, and which of them are separate processes? Name each one and its
port. Why are the Judge and Masker separate services while the Guardrail runs in-process?
Answer in your own words, not SPEC's.

**Answer (Malik, first draft):**

The security judge(A2A), Agent(ADk, MCP tool), Masker(A2A). The security judge and masker are the separate process. Because both are sent to the independent agent to maintain the abstract or run the particular piece on the different agent. The Guardrail is the ADK which check the message is safe or not so it runs in process.

## Responsibilities

Which component decides that a customer may only see their own orders, and where exactly is
that line of code or SQL? Which component may hold an API key? Which one decides a turn is
over budget? For every rule in SPEC §11, say whether it is enforced **in code**, **in a
prompt**, or **both**, and why. This table is the core of `G-ENFORCE`.

**Answer (Malik, first draft; the SPEC §11 table is still to come):**

Data and MCP tools decides the a customer only sees their own order by enforcing the ownership in the sql. API keys are stored in the .env file. toolss with T-bud which decides a turn in over budget

## Communication

How does a message travel from the CLI or browser to the database and back? Name each hop's
protocol (function call, MCP, A2A JSON-RPC, HTTP, NDJSON). Which A2A method did you
implement, and what does a verdict look like on the wire?

**Answer (Malik, first draft; A2A method and wire verdict still to come):**

web / cli --> Sanitize --> Judge(A2A) --> guardrail (ADK) --> recall(Mem0) --> Agent(ADK + MCP) --> masker(A2A) --> Save (Mem0) --> response to user

## State

What is stored, where, and for how long: sessions, memories, traces, run logs, the action
log? Which of them contain customer data, and who can read each one? What happens to
memory when a customer's order changes after the memory was saved?

**Answer (Malik, first draft):**

Sessions are log in sessions in the guardrail, memories are the long term (Database ) + Mem0 for the long term agent memory. The run logs and action logs are stored the observability pheonix. Customer data will be stored in the database and Mem0. one the order updates it will update the memory

## Trade-offs

What did each guard cost you in latency (from your traces), and was it worth it? Did you
make the Guardrail fail closed, and what does that cost when Gemini is slow? If you changed
`T-MEM-MINSCORE` or argued against any threshold, give the evidence here, and keep the
original gate in your report.

**Answer (Malik, first draft; per-guard latency comes later from real traces):**

It cost the fast resolution tiem, Frictionless support experience, i give up give up speed, consistency, and goodwill
