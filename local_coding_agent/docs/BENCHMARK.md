<!-- help: section="Reference" order=7 -->
# Model Benchmarking & Evaluation

## Methodology

The benchmark suite executes reproducible atomic coding tasks on disposable test fixtures. Local models operate through the standard proposal-only controller; patches are never written to the main checkout.

To evaluate correctness, the benchmark applies only validated patches inside an isolated temporary directory and executes a deterministic external Python oracle in a restricted child process (isolated environment, minimal permissions, allowlisted imports).

Evaluation parameters:
- `repeats: 3`
- `num_ctx: 8192`
- `temperature: 0.7`
- `top_p: 0.8`
- `presence_penalty: 1.5`
- `num_predict: 512`
- `max_turns: 4`

Metrics recorded:
- **Correctness**: Percentage of tasks where the applied patch satisfies external oracle assertions.
- **Loop Reliability**: Percentage of tasks where the model cleanly completes the tool loop with a valid final JSON response.
- **Patch Validity**: Percentage of proposed patches that successfully pass `git apply --check`.
- **Generation TPS**: Tokens generated per second during the evaluation phase.
- **Prefill TPS**: Context tokens processed per second.
- **Confidence Intervals**: 95% Wilson score confidence intervals for correctness and reliability.

---

## Benchmark Results (20-Task Extended Suite)

| Model Profile | Quant / Format | Memory (VRAM) | Correctness (95% CI) | Loop Reliability (95% CI) | Generation TPS | Recommended Role |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **`qwen3.8-27b-q4`** | Q4_K_M (17.1 GB) | ~16–24 GB | **100.0%** [83.9%, 100.0%] | **100.0%** [83.9%, 100.0%] | ~55.2 tok/s | Complex reasoning & multi-file changes |
| **`qwen3-8b-q6k`** | Q6_K (6.7 GB) | ~8–12 GB | **90.0%** [69.9%, 97.2%] | **95.0%** [76.4%, 99.1%] | **~98.4 tok/s** | Daily coding workhorse, fast refactors |
| **`qwen3-coder-30b`** | IQ2_M (10.0 GB) | ~12–16 GB | **66.7%** [43.1%, 84.5%] | **41.7%** [21.1%, 65.5%] | ~38.0 tok/s | Extreme low-RAM fallback |
| **`qwen2.5-coder`** | Q4_K_M (4.7 GB) | ~6–8 GB | **50.0%** [30.7%, 69.3%] | **50.0%** [30.7%, 69.3%] | ~75.1 tok/s | General snippet generation |

---

## Key Findings

### 1. SEARCH/REPLACE vs Unified Diff
When local models (7B–27B) were required to generate raw unified diffs (`@@ -x,y +x,y @@`), correctness was near 0% due to line-counting and hunk offset errors. Switching the tool contract to strict `SEARCH/REPLACE` blocks boosted correctness to **100%** on `qwen3.8-27b-q4` and **90%** on `qwen3-8b-q6k`.

### 2. Efficiency of `qwen3-8b-q6k`
With indentation-preserving prompt guidance and few-shot formatting examples, `qwen3-8b-q6k` achieves 90% correctness and 95% loop reliability at nearly 100 tokens/second while fitting entirely within 8 GB of VRAM.

### 3. Concurrency & Memory Sizing
- **8 GB VRAM**: Single worker running `qwen3-8b-q6k`.
- **16–24 GB VRAM**: Single worker running `qwen3.8-27b-q4` or two parallel workers on `qwen3-8b-q6k`.
- **32 GB+ VRAM**: Multiple parallel workers managed by `BoundedWorkerPool`.

---

## Capability Ladder & Intelligence Tiers (Planned R15)

To support automated routing decisions by calling host agents (e.g. Codex, Claude, Cursor), the benchmark is evolving into an adaptive multi-tier ladder:

- **Tier 0 (Syntax & Formatting)**: Indentation, constant renaming, and docstring formatting.
- **Tier 1 (Atomic Pure Functions)**: Single-function logic fixes, boundary checks, off-by-one errors.
- **Tier 2 (Single-File Refactorer)**: Multi-hunk edits within a single file up to 300 lines.
- **Tier 3 (Cross-File Coordinator)**: Synchronized 2-file changes maintaining shared public contracts.
- **Tier 4 (Algorithmic Specialist)**: Complex algorithmic edge cases with strict execution invariants.

Models are evaluated along four distinct capability dimensions:
1. **Granularity Tolerance**: Maximum digestible task scope (`atomic_hunk` vs `function` vs `file` vs `multi_file`).
2. **Polyglot Matrix**: Language-specific tier ratings (Python, TypeScript, Rust, Go).
3. **Turn Endurance / Tool Horizon**: Degradation threshold across extended multi-turn tool loops.
4. **Self-Healing Index**: Success rate in correcting proposal errors via pinpointed prescriptions.

