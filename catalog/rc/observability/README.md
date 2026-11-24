# Observability extension

This extension declares how a workload sends or exposes telemetry. Each
Condition describes one endpoint: `otlp_export` requires a compatible OTLP
receiver, while `prometheus_scrape` identifies a metrics endpoint that a scraper
must reach. A workload using both forms declares two Conditions.

The first release is [`0.1.0`](releases/0.1.0/runtimeconditions.extension.yaml).
See the [OTLP export](examples/otlp-export-profile.yaml) and
[Prometheus scrape](examples/prometheus-scrape-profile.yaml) examples.

## OTLP export

`interface.signals` is a nonempty set of `traces`, `metrics`, and `logs`. A
collector must accept every declared signal. `interface.transport` selects
`grpc`, `http/protobuf`, or `http/json`; the receiving endpoint must support
that transport and encoding. The workload sends telemetry to the receiver.

The Condition's `name` identifies the logical receiver requirement. The
fulfilling platform chooses its address and supplies runtime configuration to
the workload. A fixed collector URL, authentication credentials, exporter
headers, retries, and sampling settings are not part of this contract.

## Prometheus scrape

The workload exposes metrics on `interface.port` at the absolute HTTP
`interface.path`, using `interface.scheme` (`http` or `https`). All three fields
are required. An adapter can use them to configure discovery and scraping; the
workload must still serve a Prometheus-compatible metrics response at that
endpoint. The platform chooses the target address, scrape schedule, and any
TLS or authentication configuration.

These Conditions describe requirements, not how a particular collector,
scraper, or cluster is provisioned.
