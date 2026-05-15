# Ayla Mac App And CLI Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the current Ayla local workbench as a macOS app bundle and add an `ayla install` CLI path that installs the app and CLI into user-local locations.

**Architecture:** Keep the existing Python HTTP workspace as Ayla Core for this phase, but run it behind a native Swift/AppKit/WKWebView macOS app instead of exposing a user-managed fixed port. The CLI owns install, update, status, open, doctor, capture, and sync commands, while the App owns starting Core and rendering the visual workspace.

**Tech Stack:** Python standard library, Swift/AppKit/WebKit, macOS `.app` bundle layout, SQLite and existing `server.py`, no new third-party dependencies.

---

### Task 1: Installation Tests

**Files:**
- Create: `tests/test_ayla_cli.py`

- [ ] **Step 1: Write failing tests**

```python
def test_install_creates_mac_app_cli_and_metadata(self):
    result = ayla_cli.install(...)
    self.assertTrue(Path(result["app_path"]).exists())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests/test_ayla_cli.py -v`

Expected: FAIL because `ayla_cli` does not exist and `server.py` does not honor `AYLA_HOME`.

### Task 2: Server Data Root

**Files:**
- Modify: `server.py`

- [ ] **Step 1: Add `AYLA_HOME` support**

`VAULT_ROOT` should default to the current repo-local `agent-vault`, but use `AYLA_HOME` when set by the app or CLI.

- [ ] **Step 2: Re-run the targeted test**

Run: `python3 -m unittest tests/test_ayla_cli.py -v`

Expected: the data-root assertion passes once `ayla_cli` exists.

### Task 3: CLI And App Bundle

**Files:**
- Create: `ayla_cli.py`
- Create: `ayla`
- Create: `packaging/macos/app_launcher.py`
- Create: `macos/AylaClient/main.swift`
- Create: `scripts/build_macos_client.sh`

- [ ] **Step 1: Implement installer library functions**

Create a runtime copy under `~/Library/Application Support/Ayla/app`, skipping `.git`, `agent-vault`, caches, and test artifacts.

- [ ] **Step 2: Implement macOS app bundle generation**

Compile `macos/AylaClient/main.swift` with `swiftc` and create `Ayla.app/Contents/Info.plist`, `Ayla.app/Contents/MacOS/Ayla`, and `Ayla.app/Contents/Resources/AylaInstallRoot.txt`.

- [ ] **Step 3: Implement commands**

Support `install`, `update`, `status`, `doctor`, `open`, `start`, `stop`, `capture`, and `sync lark`.

### Task 4: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace localhost-first startup docs**

Document `./ayla install`, `ayla open`, and `ayla status`; keep manual `python3 server.py` as development mode only.

- [ ] **Step 2: Document install paths**

Document app, CLI, metadata, logs, and data root.

### Task 5: Verification

**Files:**
- Test: `tests/test_ayla_cli.py`

- [ ] **Step 1: Run unit tests**

Run: `python3 -m unittest tests/test_ayla_cli.py -v`

- [ ] **Step 2: Run isolated install smoke test**

Run: `python3 ayla_cli.py install --install-root /private/tmp/ayla-install-test --app-dir /private/tmp/ayla-app-test --bin-dir /private/tmp/ayla-bin-test --force`

- [ ] **Step 3: Inspect CLI output**

Run: `/private/tmp/ayla-bin-test/ayla status --install-root /private/tmp/ayla-install-test`
