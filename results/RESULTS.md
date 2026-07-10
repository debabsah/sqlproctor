# sqlproctor eval results

Provenance-stamped, reproducible. Each row is one (commit, model, contract) result.

`verified` / `first_blocked` describe sqlproctor's *verdicts*, not ground-truth
correctness: a query sqlproctor verifies can still be wrong if the contract does not
yet model that error. Read them as "sqlproctor-approved," not "correct."

## benchmark

| ts | git_sha | model | contract_version | contract_sha256 | wrong | caught | catch_rate | false_positives | without_accuracy | with_accuracy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-07T22:43:49Z | 5dc22b3cc | deterministic (seed 42) | v1 | dff363a1bdbd2a28b938e767fd45e5af01c800dfba97740263135bb601e220f8 | 9 | 7 | 0.778 | 0 | 0.308 | 0.846 |

## live_eval

| ts | git_sha | model | contract_version | provider | contract_sha256 | n | first_blocked | self_corrected | verified | kinds | schema | errored | effort |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-07T19:32:41Z | aca2244e1 | z-ai/glm-5.2 | v1 | openrouter | 7ca9bec9d5b67b28a6bb4379e16d28d98aaf495ba5d181d1e3a2ef3837d0d17c | 6 | 2 | 2 | 6 | {"FAN_OUT":2,"REQUIRED_FILTER":1} |  |  |  |
| 2026-07-07T20:49:15Z | 8437639cb | z-ai/glm-5.2 | v1 | openrouter | dff363a1bdbd2a28b938e767fd45e5af01c800dfba97740263135bb601e220f8 | 12 | 4 | 3 | 11 | {"FAN_OUT":2,"REQUIRED_FILTER":2,"JOIN_PATH":1} |  |  |  |
| 2026-07-08T23:21:35Z | 47a40e296 | qwen36-27b-mtp-q4km | saas-v1 | local | f497f75f6b172607aa8bc29e35fcf60bd8c7a0fffed74b3dfa2a62b384e8968b | 14 | 5 | 5 | 14 | {"METRIC":2,"JOIN_PATH":1,"REQUIRED_FILTER":3} | saas | 0 |  |
| 2026-07-08T23:38:25Z | 47a40e296 | qwen36-27b-mtp-q4km | v1 | local | dff363a1bdbd2a28b938e767fd45e5af01c800dfba97740263135bb601e220f8 | 16 | 12 | 11 | 15 | {"METRIC":2,"REQUIRED_FILTER":7,"FAN_OUT":2,"JOIN_PATH":3} | retail | 0 |  |
| 2026-07-09T00:23:13Z | 9a6ca1403 | anthropic/claude-opus-4.8 | v1 | openrouter | dff363a1bdbd2a28b938e767fd45e5af01c800dfba97740263135bb601e220f8 | 16 | 15 | 15 | 16 | {"METRIC":3,"REQUIRED_FILTER":9,"FAN_OUT":3,"JOIN_PATH":3} | retail | 0 | high |
| 2026-07-09T00:26:15Z | 9a6ca1403 | anthropic/claude-opus-4.8 | saas-v1 | openrouter | f497f75f6b172607aa8bc29e35fcf60bd8c7a0fffed74b3dfa2a62b384e8968b | 14 | 5 | 5 | 14 | {"METRIC":2,"REQUIRED_FILTER":3} | saas | 0 | high |
| 2026-07-09T00:30:15Z | 9a6ca1403 | openai/gpt-5.5 | v1 | openrouter | dff363a1bdbd2a28b938e767fd45e5af01c800dfba97740263135bb601e220f8 | 16 | 8 | 8 | 16 | {"REQUIRED_FILTER":6,"FAN_OUT":4} | retail | 0 | high |
| 2026-07-09T00:33:56Z | 9a6ca1403 | openai/gpt-5.5 | saas-v1 | openrouter | f497f75f6b172607aa8bc29e35fcf60bd8c7a0fffed74b3dfa2a62b384e8968b | 14 | 5 | 5 | 14 | {"METRIC":3,"JOIN_PATH":1,"REQUIRED_FILTER":1} | saas | 0 | high |
| 2026-07-09T00:38:35Z | 9a6ca1403 | google/gemini-3.1-pro-preview | v1 | openrouter | dff363a1bdbd2a28b938e767fd45e5af01c800dfba97740263135bb601e220f8 | 16 | 9 | 9 | 16 | {"FAN_OUT":2,"REQUIRED_FILTER":6,"JOIN_PATH":2,"METRIC":1} | retail | 0 | high |
| 2026-07-09T00:41:15Z | 9a6ca1403 | google/gemini-3.1-pro-preview | saas-v1 | openrouter | f497f75f6b172607aa8bc29e35fcf60bd8c7a0fffed74b3dfa2a62b384e8968b | 14 | 6 | 6 | 14 | {"REQUIRED_FILTER":5,"METRIC":1} | saas | 0 | high |
| 2026-07-09T00:44:57Z | 9a6ca1403 | z-ai/glm-5.2 | v1 | openrouter | dff363a1bdbd2a28b938e767fd45e5af01c800dfba97740263135bb601e220f8 | 16 | 8 | 6 | 14 | {"METRIC":3,"FAN_OUT":5,"REQUIRED_FILTER":2,"JOIN_PATH":1} | retail | 0 | high |
| 2026-07-09T00:47:46Z | 9a6ca1403 | z-ai/glm-5.2 | saas-v1 | openrouter | f497f75f6b172607aa8bc29e35fcf60bd8c7a0fffed74b3dfa2a62b384e8968b | 14 | 5 | 4 | 13 | {"METRIC":2,"JOIN_PATH":1,"REQUIRED_FILTER":2} | saas | 0 | high |
| 2026-07-09T04:02:13Z | 810bb9e33 | google/gemini-3.1-pro-preview | tpcds-v1 | openrouter | e8288a8300c7be8c1145d9be9662096b789755662e583154b92752f8bd53d699 | 14 | 3 | 3 | 14 | {"FAN_OUT":12,"JOIN_PATH":1} | tpcds | 0 | high |
| 2026-07-09T04:05:24Z | 810bb9e33 | openai/gpt-5.5 | tpcds-v1 | openrouter | e8288a8300c7be8c1145d9be9662096b789755662e583154b92752f8bd53d699 | 14 | 1 | 1 | 14 | {"FAN_OUT":2} | tpcds | 0 | high |
| 2026-07-09T04:10:00Z | 810bb9e33 | z-ai/glm-5.2 | tpcds-v1 | openrouter | e8288a8300c7be8c1145d9be9662096b789755662e583154b92752f8bd53d699 | 14 | 5 | 5 | 14 | {"SURFACE":19,"PARSE":1,"FAN_OUT":4} | tpcds | 0 | high |
