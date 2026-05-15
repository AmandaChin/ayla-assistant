import AppKit
@preconcurrency import WebKit

private let installRootResourceName = "AylaInstallRoot"

private func trimmed(_ value: String) -> String {
    value.trimmingCharacters(in: .whitespacesAndNewlines)
}

private func expandedPath(_ value: String) -> String {
    (value as NSString).expandingTildeInPath
}

private func bundledInstallRootPath() -> String? {
    guard
        let url = Bundle.main.url(forResource: installRootResourceName, withExtension: "txt"),
        let text = try? String(contentsOf: url, encoding: .utf8)
    else {
        return nil
    }

    for line in text.components(separatedBy: .newlines) {
        let value = trimmed(line)
        if !value.isEmpty && !value.hasPrefix("#") {
            return expandedPath(value)
        }
    }
    return nil
}

private func defaultInstallRoot() -> URL {
    let fallback = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
        .appendingPathComponent("Library", isDirectory: true)
        .appendingPathComponent("Application Support", isDirectory: true)
    let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first ?? fallback
    return appSupport.appendingPathComponent("Ayla", isDirectory: true)
}

private func installRoot() -> URL {
    let environment = ProcessInfo.processInfo.environment
    if let explicit = environment["AYLA_INSTALL_ROOT"], !trimmed(explicit).isEmpty {
        return URL(fileURLWithPath: expandedPath(explicit), isDirectory: true)
    }
    if let bundled = bundledInstallRootPath() {
        return URL(fileURLWithPath: bundled, isDirectory: true)
    }
    return defaultInstallRoot()
}

private func appRoot(for installRoot: URL) -> URL {
    installRoot.appendingPathComponent("app", isDirectory: true)
}

private func dataRoot(for installRoot: URL) -> URL {
    installRoot.appendingPathComponent("data", isDirectory: true)
}

private func runtimeRoot(for installRoot: URL) -> URL {
    installRoot.appendingPathComponent("runtime", isDirectory: true)
}

private func logsRoot(for installRoot: URL) -> URL {
    installRoot.appendingPathComponent("logs", isDirectory: true)
}

private func stateFile(for installRoot: URL) -> URL {
    runtimeRoot(for: installRoot).appendingPathComponent("core-state.json", isDirectory: false)
}

private func htmlEscaped(_ value: String) -> String {
    value
        .replacingOccurrences(of: "&", with: "&amp;")
        .replacingOccurrences(of: "<", with: "&lt;")
        .replacingOccurrences(of: ">", with: "&gt;")
        .replacingOccurrences(of: "\"", with: "&quot;")
}

private func readState(at url: URL) -> [String: Any] {
    guard
        let data = try? Data(contentsOf: url),
        let object = try? JSONSerialization.jsonObject(with: data),
        let state = object as? [String: Any]
    else {
        return [:]
    }
    return state
}

private func processRunning(_ pid: Int) -> Bool {
    guard pid > 0 else {
        return false
    }
    return kill(pid_t(pid), 0) == 0
}

private func healthOK(_ urlString: String, timeout: TimeInterval = 1.5) -> Bool {
    guard let url = URL(string: urlString.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/api/health") else {
        return false
    }
    var request = URLRequest(url: url)
    request.timeoutInterval = timeout

    let semaphore = DispatchSemaphore(value: 0)
    var ok = false
    let task = URLSession.shared.dataTask(with: request) { data, _, _ in
        defer { semaphore.signal() }
        guard
            let data,
            let object = try? JSONSerialization.jsonObject(with: data),
            let payload = object as? [String: Any],
            let healthy = payload["ok"] as? Bool
        else {
            return
        }
        ok = healthy
    }
    task.resume()
    if semaphore.wait(timeout: .now() + timeout) == .timedOut {
        task.cancel()
        return false
    }
    return ok
}

private func coreURLString(from state: [String: Any]) -> String? {
    guard let url = state["url"] as? String, !trimmed(url).isEmpty else {
        return nil
    }
    return url
}

private func existingCoreState(installRoot: URL) -> [String: Any]? {
    let state = readState(at: stateFile(for: installRoot))
    guard
        let url = coreURLString(from: state),
        let pid = state["pid"] as? Int,
        processRunning(pid),
        healthOK(url)
    else {
        return nil
    }
    return state
}

private func appendLogHandle(_ url: URL) -> FileHandle? {
    FileManager.default.createFile(atPath: url.path, contents: nil)
    guard let handle = try? FileHandle(forWritingTo: url) else {
        return nil
    }
    _ = try? handle.seekToEnd()
    return handle
}

private func runCoreBootstrap(installRoot: URL) {
    let launcher = appRoot(for: installRoot)
        .appendingPathComponent("packaging", isDirectory: true)
        .appendingPathComponent("macos", isDirectory: true)
        .appendingPathComponent("app_launcher.py", isDirectory: false)
    guard FileManager.default.fileExists(atPath: launcher.path) else {
        return
    }

    try? FileManager.default.createDirectory(at: runtimeRoot(for: installRoot), withIntermediateDirectories: true)
    try? FileManager.default.createDirectory(at: dataRoot(for: installRoot), withIntermediateDirectories: true)
    try? FileManager.default.createDirectory(at: logsRoot(for: installRoot), withIntermediateDirectories: true)

    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    process.arguments = ["python3", launcher.path]
    process.currentDirectoryURL = appRoot(for: installRoot)

    var environment = ProcessInfo.processInfo.environment
    environment["AYLA_INSTALL_ROOT"] = installRoot.path
    environment["AYLA_HOME"] = dataRoot(for: installRoot).path
    environment["AYLA_NO_OPEN"] = "1"
    process.environment = environment

    let logURL = logsRoot(for: installRoot).appendingPathComponent("app.log", isDirectory: false)
    if let handle = appendLogHandle(logURL) {
        process.standardOutput = handle
        process.standardError = handle
    }

    do {
        try process.run()
        process.waitUntilExit()
    } catch {
        return
    }
}

private func ensureCore(installRoot: URL) -> [String: Any] {
    if let existing = existingCoreState(installRoot: installRoot) {
        return existing
    }

    runCoreBootstrap(installRoot: installRoot)

    let deadline = Date().addingTimeInterval(10)
    while Date() < deadline {
        let state = readState(at: stateFile(for: installRoot))
        if let url = coreURLString(from: state), healthOK(url) {
            return state
        }
        Thread.sleep(forTimeInterval: 0.2)
    }
    return readState(at: stateFile(for: installRoot))
}

private func missingCoreHTML(installRoot: URL, statePath: URL) -> String {
    let root = htmlEscaped(installRoot.path)
    let state = htmlEscaped(statePath.path)
    return """
    <!doctype html>
    <html lang="zh-CN">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Ayla</title>
      <style>
        :root { color-scheme: light dark; --bg: #f5f5f7; --ink: #1d1d1f; --muted: rgba(29,29,31,.68); --card: rgba(255,255,255,.84); --border: rgba(0,0,0,.1); }
        @media (prefers-color-scheme: dark) { :root { --bg: #171a21; --ink: #f4f5f7; --muted: rgba(244,245,247,.72); --card: rgba(255,255,255,.06); --border: rgba(255,255,255,.13); } }
        * { box-sizing: border-box; }
        html, body { margin: 0; min-height: 100%; }
        body { display: grid; min-height: 100vh; place-items: center; background: var(--bg); color: var(--ink); font: 15px/1.55 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif; }
        main { width: min(720px, calc(100vw - 48px)); border: 1px solid var(--border); border-radius: 18px; background: var(--card); padding: 32px; }
        h1 { margin: 0 0 10px; font-size: 26px; letter-spacing: 0; }
        p { margin: 10px 0 0; color: var(--muted); }
        code { display: block; margin-top: 14px; padding: 12px 14px; overflow-wrap: anywhere; border-radius: 10px; background: rgba(0,0,0,.08); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
      </style>
    </head>
    <body>
      <main>
        <h1>Ayla</h1>
        <p>没有成功启动本地 Ayla Core。请在终端执行 <strong>ayla doctor</strong> 查看 Python、安装目录和日志状态。</p>
        <code>\(root)</code>
        <code>\(state)</code>
      </main>
    </body>
    </html>
    """
}

private final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow?
    private var webView: WKWebView?
    private let installRootURL = installRoot()
    private var coreURL: URL?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenu()
        buildWindow()
        loadWorkspace()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    private func buildWindow() {
        let configuration = WKWebViewConfiguration()
        let pagePreferences = WKWebpagePreferences()
        pagePreferences.allowsContentJavaScript = true
        configuration.defaultWebpagePreferences = pagePreferences

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        webView.wantsLayer = true
        if webView.responds(to: Selector(("setDrawsBackground:"))) {
            webView.setValue(false, forKey: "drawsBackground")
        }
        self.webView = webView

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.center()
        window.minSize = NSSize(width: 920, height: 620)
        window.title = "Ayla"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        if #available(macOS 11.0, *) {
            window.toolbarStyle = .unified
        }
        window.contentView = webView
        self.window = window
        window.makeKeyAndOrderFront(nil)
    }

    private func loadWorkspace() {
        let state = ensureCore(installRoot: installRootURL)
        guard let rawURL = coreURLString(from: state), let url = URL(string: rawURL) else {
            webView?.loadHTMLString(
                missingCoreHTML(installRoot: installRootURL, statePath: stateFile(for: installRootURL)),
                baseURL: nil
            )
            return
        }
        coreURL = url
        webView?.load(URLRequest(url: url))
    }

    private func isCoreURL(_ url: URL) -> Bool {
        guard let coreURL else {
            return false
        }
        return url.scheme == coreURL.scheme && url.host == coreURL.host && url.port == coreURL.port
    }

    private func openOutsideWorkspace(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased() else {
            return false
        }
        if scheme == "about" || scheme == "data" || scheme == "javascript" {
            return false
        }
        if isCoreURL(url) {
            return false
        }
        NSWorkspace.shared.open(url)
        return true
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.allow)
            return
        }
        if openOutsideWorkspace(url) {
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        guard navigationAction.targetFrame == nil, let url = navigationAction.request.url else {
            return nil
        }
        if openOutsideWorkspace(url) {
            return nil
        }
        webView.load(navigationAction.request)
        return nil
    }

    private func buildMenu() {
        let mainMenu = NSMenu()

        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu(title: "Ayla")

        let aboutItem = NSMenuItem(title: "About Ayla", action: #selector(showAbout(_:)), keyEquivalent: "")
        aboutItem.target = self
        appMenu.addItem(aboutItem)
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(NSMenuItem(title: "Quit Ayla", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appMenuItem.submenu = appMenu

        let fileMenuItem = NSMenuItem()
        mainMenu.addItem(fileMenuItem)
        let fileMenu = NSMenu(title: "File")

        let reloadItem = NSMenuItem(title: "Reload", action: #selector(reloadWorkspace(_:)), keyEquivalent: "r")
        reloadItem.target = self
        fileMenu.addItem(reloadItem)

        let browserItem = NSMenuItem(title: "Open Workspace in Browser", action: #selector(openWorkspaceInBrowser(_:)), keyEquivalent: "b")
        browserItem.target = self
        fileMenu.addItem(browserItem)

        let revealItem = NSMenuItem(title: "Reveal Ayla Data", action: #selector(revealDataFolder(_:)), keyEquivalent: "")
        revealItem.target = self
        fileMenu.addItem(revealItem)

        fileMenuItem.submenu = fileMenu

        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(NSMenuItem(title: "Undo", action: Selector(("undo:")), keyEquivalent: "z"))
        let redoItem = NSMenuItem(title: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redoItem.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(redoItem)
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(NSMenuItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        editMenu.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
        editMenuItem.submenu = editMenu

        NSApp.mainMenu = mainMenu
    }

    @objc private func reloadWorkspace(_ sender: Any?) {
        loadWorkspace()
    }

    @objc private func openWorkspaceInBrowser(_ sender: Any?) {
        if let coreURL {
            NSWorkspace.shared.open(coreURL)
        }
    }

    @objc private func revealDataFolder(_ sender: Any?) {
        NSWorkspace.shared.activateFileViewerSelecting([dataRoot(for: installRootURL)])
    }

    @objc private func showAbout(_ sender: Any?) {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.1.0"
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "Ayla",
            .applicationVersion: version,
            .version: version,
        ])
    }
}

private let application = NSApplication.shared
private let delegate = AppDelegate()
application.delegate = delegate
application.run()
