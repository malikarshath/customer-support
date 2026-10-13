Alice never sees Bob's laptop.

## BUILD_LOG

> Copy this file to `BUILD_LOG.md`. Fill in each stage **as you go**, not at the end: the
> decision **before** you prompt your agent, the prediction **before** you run the check.
>
> This is your record of what *you* understood. A person reads it for `G-DESIGN` and
> `G-ENFORCE`. Your coding agent is instructed not to write it for you; if the words aren't
> yours, it shows.
>
> Three lines per part is plenty. "I expected X, got Y, because Z" beats a paragraph.

---

## Stage 1: the database
- **I decided:** Executed `TRUNCATE TABLE actions_log, customer_orders, users RESTART IDENTITY` in `db/seed.sql` to clear tables without dropping schema definitions.
- **I predicted:** *written after*
- **What happened (paste output):** 
```text
  psql:db/seed.sql:14: NOTICE:  relation "users" already exists, skipping
  psql:db/seed.sql:24: NOTICE:  relation "customer_orders" already exists, skipping
  psql:db/seed.sql:27: NOTICE:  relation "idx_customer_orders_customer_email" already exists, skipping
  psql:db/seed.sql:37: NOTICE:  relation "actions_log" already exists, skipping
  reset ok

   users
  -------
      10

   orders | first_id | last_id
  --------+----------+---------
       17 |        1 |      17

   actions_log_rows
  ------------------
                  0
```

---

## Stage 2: the tools, and where access control lives
- **Before reading SPEC R-1, I thought ownership belonged in:** Model system instructions telling the agent to only view the logged-in user's data.
- **I decided:** Enforce ownership directly in SQL within `mcp_toolbox/tools.yaml` using parameter `$2` (`WHERE order_id = $1 AND customer_email = $2`). The session email is bound by the host application outside the model's reach.
- **What happened (order 5 as alice):** {"result": "null"}

---

## Stage 3: the agent, and a bare CLI
- **I decided (tools loaded, how the email is bound):** Tools load with the session email bound inside `support/agent.py` using `load_agent_tools` and `bound_params_for`. The model's tool schema exposes only `order_id`.
- **I predicted the "order 5 for bob" request would:** *written after*
- **What happened:**  I cannot find order 5 on your account.

---

## Stage 4: the pipeline and its events
- **My hand-sketched CLI lines:** *(written after)*
  ```text
  [judge] passed · security.a2a_judge
  [call_llm] gemini-3.5-flash-lite · call get-order-status
  [recall] 5 candidates retrieved · 5 inserted
  [final] Order 3 is SHIPPED
  ```
- **I predicted the first event after the agent stage would be:** *written after*
- **What happened:** Events emitted in exact order (`trace` → `stage` → `step` → `llm` → `tool_call` → `tool_result` → `mask` → `save` → `final`), and `runs/<turn_id>.json` was written.

---

## Stage 5: one trace per turn
- **I decided (what goes in span attributes, who can see Phoenix):** Recorded `input.value`, `output.value`, and `user.id` on root `agent.turn` spans for local debugging. Phoenix access is restricted locally to `http://localhost:6006`.
- **Trace id:** `4142709e63a295381321d2030d211a1b`
- **One thing the trace showed that the reply didn't:** The trace showed that `customer_email` was completely absent from the `execute_tool get-order-status` span arguments (`{"order_id": 3}`), proving the model never sees or passes the email, and revealed that `memory.recall` evaluated 5 candidates from Mem0.

---

## Stage 6: Sanitizer and Security Judge
- **I decided (who may block, which A2A method):** Either guard blocks independently. The local pattern scanner drops signature attacks instantly, while the Judge evaluates complex inputs over A2A `tasks/send` synchronously in the HTTP request handler.
- **Predicted vs actual X01 latency:** *written after*
- **When I stopped the Judge, my pipeline first:** Caught the connection failure in `support/a2a_client.py`, raised a `GuardError`, yielded a 502 error event, and aborted execution before `invoke_agent` ran.
- **Failing trajectory saved at:** `runs/failing/turn_54b508ecef9744cd968a.json` (trace `e2d0a0e61831e947e7bb721ba671ad1d`).

---

## Stage 7: the Guardrail
- **Three messages that must pass / three that must not (written before the prompt):** *(written after)*
  - *Must Pass:* "Has my parcel left the warehouse yet?", "Please deliver to my neighbour", "How long do refunds take?"
  - *Must Not Pass:* "Who painted the Mona Lisa?", "Compose a sonnet", "Help me with my homework"
- **False blocks / off-topic blocks, per prompt version:**

  | Version | What I changed | Legit false blocks | Off-topic blocked |
  |---|---|---|---|
  | v1 | Initial prompt with domain scope & examples | 0 | 15/15 |

---

## Stage 8: Masker and memory
- **I decided (what counts as PII, the cutoff):** Third-party emails, card numbers, and phone numbers count as PII. The user's own email remains unmasked. Default cutoff set to 0.25. *(Revised to 0.20 after eval, because five legit memories scored 0.17 to 0.24)*.
- **My planted memory's score, and whether my cutoff kept it:** Diana M01 scored 0.51 (kept), George M03 scored 0.57 (kept), Hannah M04 scored 0.29 (kept). Two memory pairs scored below 0.20.
- **What the Masker reported on my PII test:** The Masker reported count `1` and masked the assistant's reply to `[REDACTED_PHONE]` in trace `f81401af1bbaf1734b666e507c1fbbac`.

---

## Stage 9: the web UI
- **My sketch, in words:** Main chat viewport flanked by an expandable right-hand sidebar that streams pipeline progress steps, SQL tool parameters, vector recall lists, and trace links in real time as POST /api/chat streams events.
- **Something the UI shows that the CLI doesn't (feature or leak?):** The sidebar exposes internal database schemas (customer_orders), raw SQL query structures (WHERE order_id = $1 AND customer_email = $2), bound session emails, and direct Phoenix trace links. While this is an invaluable feature for developers auditing parameter isolation and latency in a local sandbox, in a production customer-facing app it represents a critical information leak because exposing table structures and SQL templates equips an attacker with the exact schema map needed to craft targeted SQL probes or prompt injections.
---

## Stage 10: the eval runner
- **How I handled the memory waits:** Parallelized evaluation pairs across different users, enforcing a 120-second wait between plant and ask steps, a 25-second settle buffer after Mem0 clear calls, and 30-second polling.
- **First run's failing rows, and what I changed:** Failed `T-LAT-P50`, `T-LAT-P95`, and `T-MEM-RECALL`. Switched the agent model to `gemini-3.5-flash-lite`, fixed an eval clear-then-plant setup race condition, and lowered `T_MEM_MINSCORE` from 0.25 to 0.20.
- **Second run:** Pending final eval run.
- **Successful turn I read end to end (trace id), and what it taught me:** `4142709e63a295381321d2030d211a1b` confirmed 7-step pipeline execution and showed `execute_tool get-order-status` fetching live SQL facts in 4 ms using bound session parameters. It also revealed a memory design flaw: `memory.recall` retrieved a misleading past clarification (*"User clarified that they meant order 4 instead of order 3"* at score 0.34). While the agent correctly prioritized SQL tools over memories as instructed by prompt rules, saving raw user messages persists transient operational chatter alongside standing preferences.
- **Failing turn I read end to end (trace id), and what it taught me:** `e2d0a0e61831e947e7bb721ba671ad1d` proved that an unreachable guard service raises a `GuardError` at span 3 (`security.a2a_judge` ERROR) and halts execution immediately, verifying memory rule R-4 and the event contract that `save` never appears on an errored turn.