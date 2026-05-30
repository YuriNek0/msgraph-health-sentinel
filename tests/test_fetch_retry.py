import unittest
from typing import cast
from unittest.mock import patch

import fetch
from endpoints import GraphEndpoint


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class FakeClient:
    def __init__(self, responses_by_endpoint: dict[str, list[object]]):
        self._responses_by_endpoint = responses_by_endpoint
        self.calls: list[str] = []

    def request(self, endpoint: GraphEndpoint) -> FakeResponse:
        self.calls.append(endpoint.name)
        response = self._responses_by_endpoint[endpoint.name].pop(0)
        if isinstance(response, fetch.RequestError):
            raise response
        if not isinstance(response, FakeResponse):
            raise TypeError("FakeClient response must be a FakeResponse or RequestError")
        return response


def make_endpoint(name: str, permissions: tuple[str, ...] = ("User.Read",)):
    return GraphEndpoint(
        name=name,
        method="GET",
        url=f"https://graph.microsoft.com/v1.0/{name}",
        required_permissions=permissions,
        docs="https://learn.microsoft.com/graph",
    )


def make_request_error(endpoint: GraphEndpoint, status_code: int) -> fetch.RequestError:
    return fetch.RequestError(
        endpoint.url,
        status_code,
        "non-success status code",
        response_text="transient failure",
    )


class PollGraphEndpointRetryTests(unittest.TestCase):
    def test_retries_in_scope_retryable_failure_after_initial_pass(self):
        retry_endpoint = make_endpoint("retryable")
        other_endpoint = make_endpoint("other")
        client = FakeClient(
            {
                retry_endpoint.name: [
                    make_request_error(retry_endpoint, 500),
                    FakeResponse(200),
                ],
                other_endpoint.name: [FakeResponse(200)],
            }
        )

        with patch.object(fetch, "ENDPOINTS", (retry_endpoint, other_endpoint)):
            with patch.object(fetch.random, "shuffle", lambda values: None):
                with patch.object(fetch.random, "randint", return_value=3):
                    with patch.object(fetch.time, "sleep") as sleep:
                        result = fetch.poll_graph_endpoints(
                            cast(fetch.GraphRequester, client), ("User.Read",)
                        )

        self.assertEqual(client.calls, ["retryable", "other", "retryable"])
        sleep.assert_called_once_with(3)
        self.assertEqual(result.failure_count, 0)
        self.assertEqual(result.success_count, 2)

    def test_yields_error_after_retryable_failure_retry_fails(self):
        endpoint = make_endpoint("retryable")
        client = FakeClient(
            {
                endpoint.name: [
                    make_request_error(endpoint, 503),
                    make_request_error(endpoint, 503),
                ],
            }
        )

        with patch.object(fetch, "ENDPOINTS", (endpoint,)):
            with patch.object(fetch.random, "shuffle", lambda values: None):
                with patch.object(fetch.random, "randint", return_value=10):
                    with patch.object(fetch.time, "sleep") as sleep:
                        result = fetch.poll_graph_endpoints(
                            cast(fetch.GraphRequester, client), ("User.Read",)
                        )

        self.assertEqual(client.calls, ["retryable", "retryable"])
        sleep.assert_called_once_with(10)
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(result.failed_endpoints[0].status_code, 503)

    def test_non_retryable_failure_is_not_retried(self):
        endpoint = make_endpoint("missing")
        client = FakeClient({endpoint.name: [make_request_error(endpoint, 404)]})

        with patch.object(fetch, "ENDPOINTS", (endpoint,)):
            with patch.object(fetch.random, "shuffle", lambda values: None):
                with patch.object(fetch.time, "sleep") as sleep:
                    result = fetch.poll_graph_endpoints(
                        cast(fetch.GraphRequester, client), ("User.Read",)
                    )

        self.assertEqual(client.calls, ["missing"])
        sleep.assert_not_called()
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(result.failed_endpoints[0].status_code, 404)

    def test_out_of_scope_retryable_failure_is_not_retried(self):
        endpoint = make_endpoint("out-of-scope", permissions=("Mail.Read",))
        client = FakeClient({endpoint.name: [make_request_error(endpoint, 429)]})

        with patch.object(fetch, "ENDPOINTS", (endpoint,)):
            with patch.object(fetch.random, "shuffle", lambda values: None):
                with patch.object(fetch.time, "sleep") as sleep:
                    result = fetch.poll_graph_endpoints(
                        cast(fetch.GraphRequester, client), ("User.Read",)
                    )

        self.assertEqual(client.calls, ["out-of-scope"])
        sleep.assert_not_called()
        self.assertEqual(result.failure_count, 0)
        self.assertEqual(len(result.untracked_failures), 1)
        self.assertEqual(result.untracked_failures[0].status_code, 429)


if __name__ == "__main__":
    unittest.main()
