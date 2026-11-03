<!-- help: section="Getting started" order=2 -->
# FAQ

Short answers to the questions the Desktop Harness raises most often. Each answer follows what the app and the controller do today.

---

## The model says it needs more context. What does it need?

A `needs_context` result means the controller could not give the model a safe starting point. It does not mean the model failed.

- **Build mode without a file.** The reply asks you to name an existing file or provide an explicit scope. The controller never guesses a scope: a local model only sees the files its task allows.
- **A queued task without a file.** The task fails with `needs_context` and the message "No unambiguous existing file scope was provided for this build task". Fill the Files field of the task, or mention an existing file in the goal.
- **A task that does not fit the context window.** The controller stops with `needs_context` and the reason `context_limit`. Narrow the task to fewer or smaller files, or raise the context length of the model.
- **The model asked a question.** Answer it in the task context and retry. The [protocol](PROTOCOL.md) allows a retry only when you changed the context or the constraints; repeating the same request is refused.

---

## Why does Apply ask for a check command?

Apply does not trust the model. A patch counts as verified only when a real test runner ran a check after the patch was written, so Apply needs at least one targeted command, for example `pytest tests/`.

The app takes the command from the task when it has one. For a build in the chat it tries to detect one from the workspace: `npm test` when `package.json` has a `test` script, `cargo test`, `go test ./...`, or `pytest` for Python projects. When nothing is found it asks you, and Apply is cancelled if you leave the field empty.

If the check fails, the controller restores the files it changed and reports the failure. The same rule is enforced on the server, so a request without a check or without a declared file scope is refused.

---

## What do the colours mean?

Colour carries meaning in the Harness; a colour never borrows another one's job.

| Colour | Meaning |
| --- | --- |
| Blue | A proposal or a note from the controller. Added lines in a diff are blue too. |
| Red | Something the controller refused or a check that failed. Deleted lines in a diff are red. |
| Amber | Waiting, not failing: checks have not run yet, or something is still loading. |
| Green | A check passed. Green appears only on the Apply button and on results a test runner produced. |

The evidence line next to Apply uses the same colours:

- **Grey** (Evidence: Not run): there is no proposal yet.
- **Amber** (Proposal ready): a patch exists, checks have not run. Apply runs them.
- **Green** (Verified by Test Runner): the declared checks passed after the patch was applied.
- **Red** (Checks failed, or Patch rejected): a check failed, or the controller rejected the patch before it reached the workspace.

The dot in the top strip describes the local engine: **blue** is online, **amber** (breathing) is loading weights or still checking, **red** is offline. The dot is blue, not green, because green is reserved for passed checks.

---

## Why does it say Offline?

The app polls the local engines every few seconds with a short timeout of 0.3 seconds:

- Ollama at `http://127.0.0.1:11434/api/tags`
- llama-server at `http://127.0.0.1:8080/v1/models`

An answer with HTTP 200 means online. HTTP 503 means the engine is loading a model. A refused connection, a timeout or any other answer shows as Offline. The top strip shows the engine that the selected model profile uses.

To fix it:

1. Open **Local inference servers** from the top strip. It shows both engines and their ports.
2. Pick a model in **Model and profile** and press **Load into VRAM**. The matching engine starts together with the model. The reply card for an offline engine has the same action as **Load model and start**.
3. If you run Ollama or llama-server yourself, start it on the default port above. The Harness checks only those two addresses.

A reply card with "Engine offline" appears when a chat request reaches no engine. Run `local-agent doctor` in a terminal to check Python, Git, Ollama, memory and models in one pass.

---

## Where can I read more?

- [Quickstart](QUICKSTART.md) for the first run.
- [MCP integration](MCP_INTEGRATION.md) to connect an agent such as Claude or Cursor.
- [Runtime tips](RUNTIME_TIPS.md) for context length, KV cache and CPU limits.
- [Protocol](PROTOCOL.md) for task statuses and retry rules.
