# Runtime setup tips

This page is a practical checklist for running the local coding agent with
Ollama or an OpenAI-compatible `llama-server`. The settings below are runtime
recommendations; the model's own card and the output of the installed binary
remain the final authority for supported templates and sampler settings.

## Start with a small, observable configuration

Change one setting at a time and keep the command line, model revision, and
profile in the run record. Begin with one request slot and a context window
that fits comfortably in available memory. Increase context or concurrency
only after checking the server's reported values and a real request.

For the agent, the useful first checks are:

```powershell
python -m local_coding_agent doctor --json
ollama ps
```

`doctor` checks the configured backend and local model catalog. `ollama ps`
shows whether a model is on the GPU, on the CPU, or split between them. A
split model can be usable, but its CPU work and latency should be treated as a
separate runtime configuration.

## Ollama context and generation limits

Ollama's current FAQ documents a default context window of 4096 tokens. The
server-wide default can be changed with `OLLAMA_CONTEXT_LENGTH`, and an API
request can set `options.num_ctx`. The agent's `num_ctx` profile value becomes
that per-request `num_ctx` option. `num_predict` is the output-token limit;
thinking tokens consume part of that output budget before the final answer.

For a one-off check:

```powershell
$body = '{"model":"qwen3:8b","messages":[{"role":"user","content":"ping"}],"stream":false,"options":{"num_ctx":8192,"num_predict":256}}'
Invoke-RestMethod http://127.0.0.1:11434/api/chat -Method Post -ContentType 'application/json' -Body $body
```

Use `OLLAMA_CONTEXT_LENGTH` for a server default when all clients should use
the same baseline. Prefer the request-level `num_ctx` when several clients or
profiles share the server. Raising the value increases KV-cache memory use;
it does not increase the model's trained context limit.

Thinking is an explicit API choice. Ollama's chat API accepts `think: true`,
`think: false`, or `think: null`; `null` leaves the selected model's default in
place. Use `/api/show` to inspect the selected model before enabling a
model-specific thinking value. The agent's `think=None` profile follows this
server-default behavior, while the named profiles keep their explicit
backward-compatible setting.

## llama-server context, slots, and speculative decoding

The current llama.cpp server reference names the context flag
`-c, --ctx-size` and the concurrency flag `-np, --parallel`. `-c 0` loads the
model's context setting. The context is a shared KV-cache pool when multiple
slots are enabled, so plan the total cache for the number of simultaneous
requests. For example, two requests that each need roughly 8192 tokens need a
larger shared context than one 8192-token request. Confirm the effective value
with `GET /props` rather than assuming the command line was accepted.

Keep `-np 1` while measuring a coding profile. Parallel slots and speculative
drafting both consume cache capacity and can reduce the context available to
each request. Speculative option names vary across llama.cpp builds; inspect
the installed binary with:

```powershell
llama-server --help | Select-String 'ctx-size|parallel|spec|draft'
```

Do not copy a speculative-decoding command from a different build without
checking its model, draft-model, and cache options first.

## KV cache and flash attention

llama-server currently exposes `--cache-type-k` and `--cache-type-v`, with
`f16` as the documented default and `q8_0` among the supported quantized
types. F16 is the conservative baseline. Q8 KV can reduce memory pressure,
but it is a quality and backend compatibility tradeoff that should be checked
on the target model.

The server exposes `-fa, --flash-attn` with `on`, `off`, and `auto`. Start with
`auto`; use `on` when the target backend and build support the required
kernels. Quantized KV is especially sensitive to backend/build support, so if
changing `--cache-type-k q8_0` or `--cache-type-v q8_0` causes an error or an
unexpected CPU fallback, return to `f16` and inspect the server log before
trying another combination.

A conservative single-slot starting point is:

```text
llama-server -m C:\models\model.gguf -c 8192 -np 1 --flash-attn auto --cache-type-k f16 --cache-type-v f16
```

Use the actual path and flags printed by the installed binary. The line above
is a baseline, not a claim that every GGUF supports the same context length or
chat template.

## Thinking and chat templates in llama-server

Current llama.cpp server builds expose Jinja chat-template processing through
`--jinja` (enabled by default), and can use a model-provided template or an
explicit `--chat-template`/`--chat-template-file`. Thinking behavior is
controlled separately through `--reasoning on|off|auto`, with
`--reasoning-format` selecting whether thoughts remain in `content` or are
returned as `reasoning_content`. `--reasoning-budget N` bounds thinking tokens:
`-1` is unrestricted, `0` ends thinking immediately, and a positive value is
the token budget.

Thinking is template-dependent. A model whose template does not support a
reasoning mode can ignore a request or emit tags as ordinary content. Treat a
response as evidence of what the server did, not as proof that a flag was
honored. Keep the reasoning budget and final-answer budget inside the request's
context budget; a long thought trace can consume the space needed for the JSON
proposal.

For a first diagnostic run, make the behavior explicit and inspect the result:

```text
llama-server -m C:\models\model.gguf -c 8192 -np 1 --jinja --reasoning auto --reasoning-budget 1024
```

If the model card specifies a different template or reasoning setting, follow
that card and record the deviation from this baseline.

## Sampling for code

Use the model card's recommended sampler values as the starting point. Keep
the profile deterministic when the task is a small patch and vary sampling
only for an explicitly measured best-of-N run. Both Ollama's Modelfile
reference and llama-server's `/props` example document `repeat_penalty` as
`1.0` by default, which is the neutral starting value for code. Raise it only
when a measured repetition problem justifies the change; it can damage code
that legitimately repeats identifiers or syntax.

After changing sampling, verify the effective settings instead of relying on
the command line alone:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/props
```

The llama-server `/props` response includes the effective context and sampler
parameters. It is read-only unless the server was started with `--props`.

## CPU load and partial GPU offload

llama-server uses `-t, --threads` for generation threads and `-tb,
--threads-batch` for prompt/batch processing. Start with a modest `-t` value
when the model is partially offloaded. A setting that drives the CPU to 100%
can compete with the network stack and desktop, even if the model is still
making progress. Reduce `-t` and `-tb`, then compare latency and host load.

For Ollama, use `ollama ps` to confirm the CPU/GPU split. Do not infer a full
GPU load from a successful HTTP response: model placement and request
completion are separate observations.

## Verify the effective runtime

Use this sequence after changing a runtime setting:

1. Run `python -m local_coding_agent doctor --json` and save the JSON result.
2. Run `ollama ps` for Ollama, or `GET http://127.0.0.1:8080/props` for
   llama-server.
3. Send one small request with `stream: false` and record the response's
   completion reason and token counts.
4. Run the focused adapter/profile checks before attributing a behavior change
   to the model.

The adapter reports a `thinking_truncated` error when an opt-in thinking
response ends because its output limit was reached. Increase the thinking
budget only after checking the available context and the server's effective
settings.

## Current references

- [Ollama chat API](https://docs.ollama.com/api/chat) — `think`, `options`,
  response completion metadata, and message fields.
- [Ollama FAQ](https://docs.ollama.com/faq) — `OLLAMA_CONTEXT_LENGTH`,
  request-level `num_ctx`, `ollama ps`, and environment configuration.
- [Ollama Modelfile reference](https://docs.ollama.com/modelfile) — `num_ctx`,
  `num_predict`, sampling parameters, and their defaults.
- [llama.cpp server reference](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
  — current `-c`, `-np`, `-t`, KV-cache, flash-attention, Jinja, reasoning,
  and `/props` options.
