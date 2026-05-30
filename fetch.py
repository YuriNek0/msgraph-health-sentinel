#!/usr/bin/env python3
"""Poll multiple Microsoft Graph endpoints and send error alerts via Graph mail."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import random
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Tuple

import requests

from endpoints import ENDPOINTS, GraphEndpoint


DEFAULT_CONFIG = Path(__file__).with_name("config.json")
TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
SEND_MAIL_ENDPOINT = "https://graph.microsoft.com/v1.0/me/sendMail"
SUCCESS_CODES: Tuple[int, ...] = (200, 201, 204, 304)
RETRYABLE_STATUS_CODES: Tuple[int, ...] = (429, 500, 501, 502, 503, 504)
RETRY_DELAY_SECONDS: Tuple[int, int] = (3, 10)
EMAIL_RATE_LIMIT_MINUTES = 720
REQUEST_ERROR_RESPONSE_MAX_CHARS = 1000


class PollerError(Exception):
    """Base exception for poller failures."""


class ConfigError(PollerError):
    """Raised when configuration is missing or invalid."""


class AuthError(PollerError):
    """Raised when token retrieval fails."""


class RequestError(PollerError):
    """Raised when a Graph API call fails."""

    def __init__(
        self,
        endpoint: str,
        status_code: int | None,
        message: str,
        response_text: str | None = None,
        request_id: str | None = None,
    ):
        self.endpoint = endpoint
        self.status_code = status_code
        self.message = message
        self.response_text = response_text
        self.request_id = request_id

        if request_id:
            message = f"{message} request_id={request_id}"

        if status_code:
            super().__init__(f"[{status_code}] {endpoint}: {message}")
            return
        super().__init__(f"{endpoint}: {message}")


@dataclass(frozen=True)
class PollerConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    username: str
    password: str
    redirect_uri: str
    alert_recipients: tuple[str, ...]
    scopes: tuple[str, ...]
    required_success_count: int = 6
    error_threshold: int = 3
    poll_delay_seconds: tuple[int, int] = (500, 1000)
    request_timeout_seconds: int = 30
    connectivity_host: str = "1.1.1.1"
    connectivity_port: int = 53
    connectivity_timeout_seconds: int = 5
    email_cooldown_minutes: int = 60
    token_refresh_window_seconds: int = 120

    @property
    def token_url(self) -> str:
        return TOKEN_URL.format(tenant_id=self.tenant_id)


@dataclass(frozen=True)
class EndpointResult:
    endpoint: GraphEndpoint
    ok: bool
    status_code: int | None = None
    error: str | None = None
    response_text: str | None = None
    request_id: str | None = None
    count_in_alerts: bool = True


@dataclass(frozen=True)
class PollResult:
    endpoint_results: tuple[EndpointResult, ...]

    @property
    def success_count(self) -> int:
        return sum(
            1 for item in self.endpoint_results if item.count_in_alerts and item.ok
        )

    @property
    def tracked_endpoint_count(self) -> int:
        return sum(1 for item in self.endpoint_results if item.count_in_alerts)

    @property
    def failure_count(self) -> int:
        return sum(
            1 for item in self.endpoint_results if item.count_in_alerts and not item.ok
        )

    @property
    def failed_endpoints(self) -> tuple[EndpointResult, ...]:
        return tuple(
            item
            for item in self.endpoint_results
            if item.count_in_alerts and not item.ok
        )

    @property
    def untracked_endpoints(self) -> tuple[EndpointResult, ...]:
        return tuple(item for item in self.endpoint_results if not item.count_in_alerts)

    @property
    def untracked_successes(self) -> tuple[EndpointResult, ...]:
        return tuple(
            item
            for item in self.endpoint_results
            if not item.count_in_alerts and item.ok
        )

    @property
    def untracked_failures(self) -> tuple[EndpointResult, ...]:
        return tuple(
            item
            for item in self.endpoint_results
            if not item.count_in_alerts and not item.ok
        )


class GraphRequester(Protocol):
    def request(self, endpoint: GraphEndpoint) -> requests.Response: ...


@dataclass(frozen=True)
class DeferredRetry:
    endpoint: GraphEndpoint
    result_index: int


def _scope_is_covered(endpoint: GraphEndpoint, granted_scopes: set[str]) -> bool:
    return set(endpoint.required_permissions).issubset(granted_scopes)


def _should_retry_endpoint_error(is_in_scope: bool, exc: RequestError) -> bool:
    return is_in_scope and exc.status_code in RETRYABLE_STATUS_CODES


def _endpoint_result_from_response(
    endpoint: GraphEndpoint,
    response: requests.Response,
    count_in_alerts: bool,
) -> EndpointResult:
    return EndpointResult(
        endpoint=endpoint,
        ok=True,
        status_code=response.status_code,
        count_in_alerts=count_in_alerts,
    )


def _endpoint_result_from_error(
    endpoint: GraphEndpoint,
    exc: RequestError,
    count_in_alerts: bool,
) -> EndpointResult:
    return EndpointResult(
        endpoint=endpoint,
        ok=False,
        status_code=exc.status_code,
        error=exc.message,
        response_text=exc.response_text,
        request_id=exc.request_id,
        count_in_alerts=count_in_alerts,
    )


def _log_request_error(endpoint: GraphEndpoint, exc: RequestError, label: str) -> None:
    print(f"[{label}] {endpoint.name} -> {exc}")
    if exc.response_text:
        print(f"[{label}-response] {exc.endpoint} -> {exc.response_text}")


def _build_missing_scope_report(granted_scopes: set[str]) -> dict[str, tuple[str, ...]]:
    missing_scopes_by_endpoint: dict[str, tuple[str, ...]] = {}
    for endpoint in ENDPOINTS:
        missing = tuple(sorted(set(endpoint.required_permissions) - granted_scopes))
        if missing:
            missing_scopes_by_endpoint[endpoint.name] = missing
    return missing_scopes_by_endpoint


def _coerce_scopes(raw_scopes: object) -> tuple[str, ...]:
    if isinstance(raw_scopes, str):
        return tuple(scope for scope in raw_scopes.split() if scope)
    if isinstance(raw_scopes, tuple):
        return tuple(str(scope).strip() for scope in raw_scopes if str(scope).strip())
    if isinstance(raw_scopes, list):
        return tuple(str(scope).strip() for scope in raw_scopes if str(scope).strip())
    raise ConfigError("scopes must be a space-separated string or list/tuple")


def _coerce_recipients(raw_recipients: object) -> tuple[str, ...]:
    if isinstance(raw_recipients, str):
        recipients = [raw_recipients]
    elif isinstance(raw_recipients, tuple):
        recipients = list(raw_recipients)
    elif isinstance(raw_recipients, list):
        recipients = list(raw_recipients)
    else:
        raise ConfigError(
            "alert_recipient must be a string or list/tuple of email addresses"
        )

    cleaned = []
    for recipient in recipients:
        if not isinstance(recipient, str):
            raise ConfigError("alert_recipient must contain only email address strings")
        address = recipient.strip()
        if address:
            cleaned.append(address)

    if not cleaned:
        raise ConfigError("at least one alert recipient is required")

    return tuple(cleaned)


def _coerce_poll_delay(raw_delay: object) -> tuple[int, int]:
    if raw_delay is None:
        return (500, 1000)
    if isinstance(raw_delay, int):
        return (int(raw_delay), int(raw_delay))
    if not isinstance(raw_delay, dict):
        raise ConfigError("poll_delay_seconds must be a dictionary with min/max values")
    min_delay = int(raw_delay.get("min", raw_delay.get("min_seconds", 500)))
    max_delay = int(raw_delay.get("max", raw_delay.get("max_seconds", 1000)))
    if min_delay <= 0 or max_delay <= 0:
        raise ConfigError("poll_delay_seconds values must be positive")
    if min_delay > max_delay:
        raise ConfigError("poll_delay_seconds.min must be less than or equal to max")
    return (min_delay, max_delay)


def load_config(path: str | None = None) -> PollerConfig:
    config_path = Path(
        path or os.getenv("MSGRAPH_HEALTH_SENTINEL_CONFIG_PATH", str(DEFAULT_CONFIG))
    )
    if not config_path.exists():
        raise ConfigError(
            f"Config file not found: {config_path}. Copy config.example.json to config.json and fill credentials."
        )

    with config_path.open("r", encoding="utf-8") as fp:
        raw = json.load(fp)

    required_fields = {
        "tenant_id",
        "client_id",
        "client_secret",
        "username",
        "password",
        "redirect_uri",
        "alert_recipient",
        "scopes",
    }
    missing = required_fields - raw.keys()
    if missing:
        raise ConfigError(
            f"Config file missing required keys: {', '.join(sorted(missing))}"
        )

    poll_delay = _coerce_poll_delay(raw.get("poll_delay_seconds"))
    required_success_count = int(raw.get("required_success_count", 6))
    if required_success_count <= 0:
        raise ConfigError("required_success_count must be greater than 0")
    if required_success_count > len(ENDPOINTS):
        raise ConfigError(
            "required_success_count cannot be larger than number of configured endpoints"
        )
    error_threshold = int(raw.get("error_threshold", 3))
    if error_threshold < 1:
        raise ConfigError("error_threshold must be at least 1")
    config = PollerConfig(
        tenant_id=str(raw["tenant_id"]).strip(),
        client_id=str(raw["client_id"]).strip(),
        client_secret=str(raw["client_secret"]).strip(),
        username=str(raw["username"]).strip(),
        password=str(raw["password"]).strip(),
        redirect_uri=str(raw["redirect_uri"]).strip(),
        alert_recipients=_coerce_recipients(raw["alert_recipient"]),
        scopes=_coerce_scopes(raw["scopes"]),
        required_success_count=required_success_count,
        error_threshold=error_threshold,
        poll_delay_seconds=poll_delay,
        request_timeout_seconds=int(raw.get("request_timeout_seconds", 30)),
        connectivity_host=str(raw.get("connectivity_host", "1.1.1.1")).strip(),
        connectivity_port=int(raw.get("connectivity_port", 53)),
        connectivity_timeout_seconds=int(raw.get("connectivity_timeout_seconds", 5)),
        email_cooldown_minutes=int(raw.get("email_cooldown_minutes", 60)),
        token_refresh_window_seconds=int(raw.get("token_refresh_window_seconds", 120)),
    )

    if not config.tenant_id or not config.client_id:
        raise ConfigError("tenant_id and client_id are required")

    return config


def is_connected(host: str, port: int, timeout: int) -> bool:
    with contextlib.suppress(OSError):
        with socket.create_connection((host, port), timeout=timeout):
            return True
    return False


def acquire_instance_lock(label: str = "msgraph-health-sentinel"):
    lock_file = Path(f"/tmp/{label}.lock").open("a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    return lock_file


class GraphClient:
    def __init__(self, config: PollerConfig):
        self._config = config
        self._session = requests.Session()
        self._session.trust_env = False
        self._access_token = ""
        self._token_expires_at = dt.datetime.fromtimestamp(0, tz=dt.timezone.utc)

    def _token_is_valid(self) -> bool:
        return (
            bool(self._access_token)
            and dt.datetime.now(tz=dt.timezone.utc) < self._token_expires_at
        )

    def _authenticate(self) -> None:
        data = {
            "grant_type": "password",
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "username": self._config.username,
            "password": self._config.password,
            "scope": " ".join(self._config.scopes),
            "redirect_uri": self._config.redirect_uri,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            response = self._session.post(
                self._config.token_url,
                data=data,
                headers=headers,
                timeout=self._config.request_timeout_seconds,
            )
            if response.status_code != 200:
                print("Auth Error:")
                print(response.text)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise AuthError(f"token request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise AuthError("token response is not valid JSON") from exc

        access_token = payload.get("access_token")
        if not access_token:
            raise AuthError("access_token is missing in token response")

        expires_in = int(payload.get("expires_in", 3600))
        expires_at = dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(
            seconds=max(60, expires_in - self._config.token_refresh_window_seconds)
        )

        self._access_token = str(access_token)
        self._token_expires_at = expires_at
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            }
        )

    def request(self, endpoint: GraphEndpoint) -> requests.Response:
        if not self._token_is_valid():
            self._authenticate()

        try:
            response = self._session.request(
                endpoint.method,
                endpoint.url,
                timeout=self._config.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RequestError(endpoint.url, None, f"network error ({exc})") from exc

        if response.status_code not in SUCCESS_CODES:
            raise RequestError(
                endpoint.url,
                response.status_code,
                "non-success status code",
                response_text=response.text[:REQUEST_ERROR_RESPONSE_MAX_CHARS],
                request_id=response.headers.get("request-id")
                or response.headers.get("client-request-id"),
            )
        return response

    def send_error_email(self, subject: str, body: str) -> None:
        if not self._config.alert_recipients:
            raise RequestError(
                SEND_MAIL_ENDPOINT, None, "alert_recipient is empty in config"
            )

        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "Text",
                    "content": body,
                },
                "toRecipients": [
                    {"emailAddress": {"address": recipient}}
                    for recipient in self._config.alert_recipients
                ],
            },
            "saveToSentItems": True,
        }

        try:
            response = self._session.post(
                SEND_MAIL_ENDPOINT,
                json=payload,
                timeout=self._config.request_timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            response = getattr(exc, "response", None)
            response_text = (
                None
                if response is None
                else response.text[:REQUEST_ERROR_RESPONSE_MAX_CHARS]
            )
            request_id = None
            if response is not None:
                request_id = response.headers.get("request-id") or response.headers.get(
                    "client-request-id"
                )
            raise RequestError(
                SEND_MAIL_ENDPOINT,
                None if response is None else response.status_code,
                f"email delivery failed ({exc})",
                response_text=response_text,
                request_id=request_id,
            ) from exc


def poll_graph_endpoints(
    client: GraphRequester, config_scopes: tuple[str, ...]
) -> PollResult:
    endpoints = list(ENDPOINTS)
    random.shuffle(endpoints)
    scope_set = set(config_scopes)

    results: list[EndpointResult | None] = []
    deferred_retries: list[DeferredRetry] = []
    for endpoint in endpoints:
        should_count = _scope_is_covered(endpoint, scope_set)
        try:
            response = client.request(endpoint)
            status_label = "ok" if should_count else "warn"
            print(f"[{status_label}] {endpoint.name} -> {response.status_code}")
            results.append(
                _endpoint_result_from_response(endpoint, response, should_count)
            )
        except RequestError as exc:
            if _should_retry_endpoint_error(should_count, exc):
                print(
                    f"[retry-pending] {endpoint.name} -> {exc}. "
                    "Will retry after initial endpoint pass."
                )
                results.append(None)
                deferred_retries.append(
                    DeferredRetry(endpoint=endpoint, result_index=len(results) - 1)
                )
                continue

            label = "warn" if not should_count else "error"
            _log_request_error(endpoint, exc, label)
            results.append(_endpoint_result_from_error(endpoint, exc, should_count))

    for retry in deferred_retries:
        delay = random.randint(*RETRY_DELAY_SECONDS)
        print(f"[retry-delay] {retry.endpoint.name} -> sleeping {delay} seconds")
        time.sleep(delay)
        try:
            response = client.request(retry.endpoint)
            print(f"[retry-ok] {retry.endpoint.name} -> {response.status_code}")
            results[retry.result_index] = _endpoint_result_from_response(
                retry.endpoint,
                response,
                count_in_alerts=True,
            )
        except RequestError as exc:
            _log_request_error(retry.endpoint, exc, "error")
            results[retry.result_index] = _endpoint_result_from_error(
                retry.endpoint,
                exc,
                count_in_alerts=True,
            )

    return PollResult(tuple(item for item in results if item is not None))


def should_send_alert(
    last_sent: dt.datetime | None,
    config: PollerConfig,
) -> bool:
    if not config.alert_recipients:
        return False
    if last_sent is None:
        return True
    cooldown_minutes = max(config.email_cooldown_minutes, EMAIL_RATE_LIMIT_MINUTES)
    return dt.datetime.now(tz=dt.timezone.utc) >= last_sent + dt.timedelta(
        minutes=cooldown_minutes
    )


def build_failure_report(result: PollResult) -> str:
    out_of_scope_successes = result.untracked_successes
    out_of_scope_failures = result.untracked_failures
    out_of_scope_success_count = len(out_of_scope_successes)
    out_of_scope_failure_count = len(out_of_scope_failures)
    lines = [
        f"In-scope failed endpoints: {result.failure_count}",
        f"Successful endpoints: {result.success_count}/{result.tracked_endpoint_count}",
        f"Out-of-scope successful endpoints: {out_of_scope_success_count}",
        f"Out-of-scope failed endpoints: {out_of_scope_failure_count}",
        "",
    ]
    if result.failed_endpoints:
        lines.extend(
            [
                "",
                "In-scope failed endpoint details:",
            ]
        )
        for item in result.failed_endpoints:
            status = item.status_code if item.status_code else "no response"
            lines.append(
                f"- {item.endpoint.name} ({item.endpoint.url}) [{status}] {item.error}"
            )
            if item.request_id:
                lines.append(f"  request_id: {item.request_id}")
            if item.response_text:
                lines.append(f"  response: {item.response_text}")

    if out_of_scope_successes:
        lines.extend(
            [
                "",
                "Out-of-scope successful endpoint details:",
            ]
        )
        for item in out_of_scope_successes:
            status = item.status_code if item.status_code else "no response"
            lines.append(f"- {item.endpoint.name} ({item.endpoint.url}) [{status}] OK")

    if out_of_scope_failures:
        lines.extend(
            [
                "",
                "Out-of-scope failed endpoint details:",
            ]
        )
        for item in out_of_scope_failures:
            status = item.status_code if item.status_code else "no response"
            lines.append(
                f"- {item.endpoint.name} ({item.endpoint.url}) [{status}] {item.error}"
            )
            if item.request_id:
                lines.append(f"  request_id: {item.request_id}")
            if item.response_text:
                lines.append(f"  response: {item.response_text}")

    return "\n".join(lines)


def run(config: PollerConfig, one_shot: bool = False, use_lock: bool = True) -> None:
    lock_file = acquire_instance_lock() if use_lock else None
    if use_lock and lock_file is None:
        print("Another poller instance is already running. Exiting.")
        return

    try:
        client = GraphClient(config)
        granted_scopes = set(config.scopes)
        missing_scope_report = _build_missing_scope_report(granted_scopes)
        if missing_scope_report:
            print("WARNING: configured scopes do not cover all polled endpoints.")
            print(
                "The following endpoints will still be called, but will not affect alerting:"
            )
            for endpoint_name, missing_scopes in sorted(missing_scope_report.items()):
                print(
                    f"- {endpoint_name}: missing scope(s): {', '.join(missing_scopes)}"
                )

        last_error_email: dt.datetime | None = None

        while True:
            if not is_connected(
                config.connectivity_host,
                config.connectivity_port,
                config.connectivity_timeout_seconds,
            ):
                print("No outbound network connectivity. Retrying in 15 seconds")
                time.sleep(15)
                continue

            result = poll_graph_endpoints(client, config.scopes)
            effective_required_success_count = min(
                config.required_success_count, max(1, result.tracked_endpoint_count)
            )

            if result.failure_count:
                print(
                    f"Poll had in-scope failures: {result.failure_count}. "
                    f"{result.success_count}/{result.tracked_endpoint_count} "
                    f"tracked endpoints succeeded (out of {len(result.endpoint_results)} total)"
                )
            elif result.success_count >= effective_required_success_count:
                print(
                    f"Poll succeeded: {result.success_count}/{result.tracked_endpoint_count} "
                    f"tracked endpoints succeeded (out of {len(result.endpoint_results)} total)"
                )
            else:
                print(
                    f"Poll failed: {result.success_count}/{result.tracked_endpoint_count} "
                    f"tracked endpoints succeeded (out of {len(result.endpoint_results)} total)"
                )

            should_send_alert_email = bool(
                result.failure_count > 0 or result.untracked_successes
            )
            if should_send_alert_email and should_send_alert(
                last_error_email,
                config,
            ):
                body = build_failure_report(result)
                try:
                    client.send_error_email("MS Graph Polling Alert", body)
                    last_error_email = dt.datetime.now(tz=dt.timezone.utc)
                    print("Failure alert email sent via Graph")
                except RequestError as exc:
                    print(f"Unable to send failure alert email: {exc}")

            if one_shot:
                break

            delay = random.randint(*config.poll_delay_seconds)
            time.sleep(delay)
    finally:
        if lock_file is not None:
            lock_file.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll Microsoft Graph endpoints")
    parser.add_argument("--config", help="Path to JSON config file")
    parser.add_argument(
        "--once", action="store_true", help="Run one polling cycle and exit"
    )
    parser.add_argument(
        "--oneshot",
        action="store_true",
        help="Alias of --once, and do not use the lock file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    one_shot = args.once or args.oneshot
    try:
        config = load_config(args.config)
        run(config, one_shot=one_shot, use_lock=not args.oneshot)
    except PollerError as exc:
        print(f"poller failed: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("stopping")


if __name__ == "__main__":
    main()
