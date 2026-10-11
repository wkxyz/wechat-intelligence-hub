# Existing Bot host acceptance — complete

Plan: docs/superpowers/plans/2026-10-11-bot-host.md
Design: docs/superpowers/specs/2026-10-11-bot-host-design.md

Ruling: Use the selected existing Dot/Grok host route; no separate chat UI or paid model loop. A cloud host needs its supported local executor or an explicitly authorized private route.

- Task 1 complete: fixed-loopback client, pre-network validation, private binding and token-free config proved. Initial missing-client failures observed before implementation.
- Task 2 complete: bounded MCP stdio, native tool modes and GUI export. Missing-mode/module and missing-export failures observed before implementation, then green.
- Task 3 complete: PR #1, #2 and #3 merged into main. Three-platform immutable release published.

Fresh-context independent review found Windows fixture ACL and recursive JSON decoder defects. Both were fixed; a subprocess regression confirms a deep invalid request returns a sanitized parse error and the server continues serving.

Validation:
- 34 local non-socket connector/client/protocol/real-worker tests passed using the installed SQLCipher runtime.
- Existing real account passed strict read-only readiness and one bounded live query. Only success/count/coverage were emitted; no account paths, message text, keys or tokens disclosed.
- Linux CI: 46 tests passed, including actual loopback pairing → separate MCP process → query → revocation, and 7 native GUI tests.
- Windows x64, macOS arm64, macOS x64: packaged dependency/window smoke checks passed; 46 discovered tests, 39 passed and 7 graphical tests intentionally skipped (those passed on Linux). Tests launch the actual packaged executable.
- Final branch CI: https://github.com/wkxyz/wechat-intelligence-hub/actions/runs/38104700053 at 862d1f7d55e80a59d781fd5271e276058996b20b.
- Main release CI: https://github.com/wkxyz/wechat-intelligence-hub/actions/runs/38104926884 passed at a5febd70c6ac378e2ed986ef08f3ff2ff49f1393.
- Release: https://github.com/wkxyz/wechat-intelligence-hub/releases/tag/wechat-bot-v0.10.0. Public non-draft tag targets the verified main commit; three ZIP assets and SHA256SUMS.txt uploaded.

Boundaries: Packages are unsigned; no automatic key acquisition, model calls, public listener or automatic tunnel. Source verification does not guarantee every WeChat version. The user's selected host must load its local tool configuration and complete actual conversation acceptance; existing Grok Bot skill availability was inspected, but no Grok settings changed or messages sent during this repository work.
