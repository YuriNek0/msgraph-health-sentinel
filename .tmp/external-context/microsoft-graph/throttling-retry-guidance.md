---
source: Official Microsoft Learn
library: Microsoft Graph
package: microsoft-graph
topic: throttling and retry guidance
fetched: 2026-05-29T00:00:00Z
official_docs: https://learn.microsoft.com/en-us/graph/throttling
---

## Implementation guidance

- **429 Too Many Requests** = throttled.
- **Honor `Retry-After`** from the failed response; wait that many seconds, then retry.
- If **`Retry-After` is missing**, use **exponential backoff** (SDKs already do this for many non-batched calls).
- Keep retrying a 429 using the latest `Retry-After` until it succeeds.
- For **batch requests**, retry only the failed sub-requests using each sub-response’s `retry-after` value.

## 5xx / transient failures

- Graph explicitly documents **500**, **503**, and **504** as server-side failures.
- **503 Service Unavailable** may include `Retry-After`; honor it.
- Treat 5xx as transient and retry with backoff, but avoid rapid-fire retries.

## Safe polling / GET patterns

- Repeated polling and full scans are likely to be throttled.
- Prefer **delta query** or **change notifications** instead of tight polling loops.
- If you must poll a GET endpoint, keep it sparse and retry only on transient failures/429s with backoff.

## `POST /me/sendMail` caution

- `sendMail` returns **202 Accepted** when Graph has accepted the request, not when delivery is complete.
- Delivery is still subject to Exchange Online limits/throttling.
- Avoid blind automatic replays after an ambiguous success; a retry can duplicate a message if the first request already reached Graph.

## Official docs

- https://learn.microsoft.com/en-us/graph/throttling
- https://learn.microsoft.com/en-us/graph/errors
- https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/outlook-things-to-know-about-send-mail
