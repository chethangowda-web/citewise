Corpus: 24 documents, 31 chunks. Queries: 74 (11 confusable, 28 keyword, 35 paraphrase).

| Method | Hit@1 | Hit@3 | Hit@5 | MRR | Beats / loses to previous* | Retrieval latency |
|---|---|---|---|---|---|---|
| Vector only | 86% | 99% | 100% | 0.929 | 0 / 0 | 11 ms |
| Keyword only (AND) | 3% | 3% | 3% | 0.027 | 0 / 72 | 9 ms |
| Keyword only (OR) | 65% | 93% | 97% | 0.787 | 6 / 25 | 16 ms |
| Hybrid, previous behaviour* | 86% | 99% | 100% | 0.929 | - | 18 ms |
| Hybrid, AND keywords | 86% | 99% | 100% | 0.929 | 0 / 0 | 16 ms |
| Hybrid, OR keywords | 93% | 99% | 100% | 0.962 | 7 / 3 | 18 ms |
| Vector + reranker | 92% | 100% | 100% | 0.957 | 7 / 2 | 480 ms |
| Hybrid OR + reranker | 92% | 100% | 100% | 0.957 | 7 / 2 | 489 ms |

Hit@1 by query type:

| Method | confusable (11) | keyword (28) | paraphrase (35) |
|---|---|---|---|
| Vector only | 55% | 96% | 89% |
| Keyword only (AND) | 0% | 7% | 0% |
| Keyword only (OR) | 55% | 79% | 57% |
| Hybrid, previous behaviour* | 55% | 96% | 89% |
| Hybrid, AND keywords | 55% | 96% | 89% |
| Hybrid, OR keywords | 91% | 96% | 91% |
| Vector + reranker | 82% | 100% | 89% |
| Hybrid OR + reranker | 82% | 100% | 89% |

\* the retriever as it shipped before this lab: full-text matches (AND semantics) could only boost vector hits.
