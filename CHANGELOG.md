# Changelog

## 0.1.0a0 - 2026-03-07

- Restructured the lab prototype into the `eebus_sdk` package.
- Added an asyncio-based SHIP session API and a high-level `HemsClient`.
- Added the `eebus` CLI for discovery, identity management, live sessions, trace summaries, and replay validation.
- Added a local `selftest` command and loopback harness to exercise both transport and application roles on one PC.
- Added sanitized interoperability fixtures and expanded the unit test suite.
- Added packaging metadata, compatibility guidance, and a CI workflow.
