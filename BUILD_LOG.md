Alice never sees Bob's laptop.

# BUILD_LOG

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
- **I decided:** TRUNCATE TABLE
- **I predicted:**
- **What happened (paste output):**

## Stage 2: the tools, and where access control lives
- **Before reading SPEC R-1, I thought ownership belonged in:**
- **I decided:** ownership must be enforced directly in the SQL statement using bound parameters injected by the host environment, completely out of the model's reach. The raw input query enters the system as an unowned, untrusted text block. The moment it passes R-1 (Input Sanitization), the application layer explicitly binds the authenticated session email to the execution context. The tool definitions handle the rest deterministically (e.g., WHERE customer_email = $1), making it cryptographically impossible for a prompt injection attack to leak another customer's order.
- **What happened (order 5 as alice):**

## Stage 3: the agent, and a bare CLI
- **I decided (tools loaded, how the email is bound):** the Agent will receive the email from the log in session. The email argument is completely fixed when the tools load. The model cannot pass an email argument because the email field is not exposed in the tool schemas. The execution context enforces a hard-bound constraint using toolbox-core, binding the authenticated email from the login session directly to the underlying SQL parameters out of the model's reach.
- **I predicted the "order 5 for bob" request would:**
- **What happened:**

## Stage 4: the pipeline and its events
- **My hand-sketched CLI lines:**
- **I predicted the first event after the agent stage would be:**
- **What happened:**

## Stage 5: one trace per turn
- **I decided (what goes in span attributes, who can see Phoenix):**
- **Trace id:**
- **One thing the trace showed that the reply didn't:**

## Stage 6: Sanitizer and Security Judge
- **I decided (who may block, which A2A method):** The user message violates then it will be blocked by the judge and A2A method

  Blocking Logic: Either one can block on its own. If the local pattern tool matches an explicit attack signature, it short-circuits execution instantly to save latency and token costs. If it passes, the out-of-process LLM Judge evaluates the payload independently. They do not need to agree; a block verdict from either layer immediately halts the request pipeline.
  A2A Wire Method: The out-of-process Judge service implements the tasks/send method to manage evaluation tasks asynchronously.
- **Predicted vs actual X01 latency:**
- **When I stopped the Judge, my pipeline first:**
- **Failing trajectory saved at:**

## Stage 7: the Guardrail
- **Three messages that must pass / three that must not (written before the prompt):**
- **False blocks / off-topic blocks, per prompt version:**

  | Version | What I changed | Legit false blocks | Off-topic blocked |
  |---|---|---|---|
  | v1 | | | |

## Stage 8: Masker and memory
- **I decided (what counts as PII, the cutoff):** the email. phone , location and SSN or PAN are considered as the PII data.

  Masking Target: Replies will mask only other people's data. The customer's own authenticated email and address remain unmasked. This preserves critical grounding utility for answers like "Your order ships to 123 Market St" instead of destroying the text into an unusable [REDACTED_LOCATION] token.
  Memory Cutoff: The memory cutoff floor will be kept at 0.25. This maintains alignment with the reference build's baseline, successfully preventing irrelevant chatter from inserting noise into context while letting true contact preferences pass.
- **My planted memory's score, and whether my cutoff kept it:**
- **What the Masker reported on my PII test:**

## Stage 9: the web UI
- **My sketch, in words:**
- **Something the UI shows that the CLI doesn't (feature or leak?):**

## Stage 10: the eval runner
- **How I handled the memory waits:** To keep the T-MEM-WAIT ≥ 120s requirement from stretching the evaluation run into hours, you must parallelize the test execution by overlapping the wait times rather than running the memory evaluation pairs sequentially.
- **First run's failing rows, and what I changed:**
- **Second run: see `reports/eval.json` (don't retype numbers here).**
- **Successful turn I read end to end (trace id), and what it taught me:**
- **Failing turn I read end to end (trace id), and what it taught me:**
