import Cocoa

func projectRoot() -> String {
    let parent = (Bundle.main.bundlePath as NSString).deletingLastPathComponent
    var directory = parent
    for _ in 0..<8 {
        let launch = (directory as NSString).appendingPathComponent("scripts/launch_dashboard.sh")
        let compose = (directory as NSString).appendingPathComponent("docker-compose.yml")
        if FileManager.default.isExecutableFile(atPath: launch),
           FileManager.default.fileExists(atPath: compose) {
            return directory
        }
        directory = (directory as NSString).deletingLastPathComponent
    }
    return parent
}

struct CommandResult {
    let code: Int32
    let output: String
}

func runCommand(_ launchPath: String, arguments: [String], directory: String) -> CommandResult {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: launchPath)
    process.arguments = arguments
    process.currentDirectoryURL = URL(fileURLWithPath: directory)
    let stdout = Pipe()
    let stderr = Pipe()
    process.standardOutput = stdout
    process.standardError = stderr
    do {
        try process.run()
        process.waitUntilExit()
        let out = String(data: stdout.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        let err = String(data: stderr.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        return CommandResult(
            code: process.terminationStatus,
            output: (err + "\n" + out).trimmingCharacters(in: .whitespacesAndNewlines)
        )
    } catch {
        return CommandResult(code: 1, output: error.localizedDescription)
    }
}

func isHealthy() -> Bool {
    runCommand(
        "/usr/bin/curl",
        arguments: ["--fail", "--silent", "--show-error", "--max-time", "2", "http://127.0.0.1:8787/healthz"],
        directory: projectRoot()
    ).code == 0
}

func statusFromFailure(_ output: String) -> String {
    let line = output.split(whereSeparator: \.isNewline).last.map(String.init) ?? ""
    if line.contains("Docker") {
        return line.hasPrefix("状态：") ? line : "状态：\(line)"
    }
    if line.isEmpty {
        return "状态：启动失败"
    }
    return "状态：\(String(line.prefix(42)))"
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow!
    private var statusField: NSTextField!
    private var runButton: NSButton!
    private var stopButton: NSButton!
    private var busy = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildWindow()
        refreshStatus()
        Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            self?.refreshStatus()
        }
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        window.center()
        runTapped()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    private func buildWindow() {
        let rect = NSRect(x: 0, y: 0, width: 380, height: 196)
        window = NSWindow(
            contentRect: rect,
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = "观察台"
        window.isReleasedWhenClosed = false
        window.level = .floating

        let content = NSView(frame: rect)
        window.contentView = content

        let title = makeLabel("观察台", font: NSFont.systemFont(ofSize: 22, weight: .semibold), frame: NSRect(x: 24, y: 142, width: 332, height: 28))
        statusField = makeLabel("状态：检查中…", font: NSFont.systemFont(ofSize: 13), frame: NSRect(x: 24, y: 112, width: 332, height: 20))
        let note = makeLabel(
            "仅作筛选 · 不提供投资建议 · 不自动下单",
            font: NSFont.systemFont(ofSize: 11),
            frame: NSRect(x: 24, y: 20, width: 332, height: 16)
        )
        note.textColor = NSColor.secondaryLabelColor

        runButton = NSButton(frame: NSRect(x: 24, y: 56, width: 156, height: 36))
        runButton.title = "运行"
        runButton.bezelStyle = .rounded
        runButton.target = self
        runButton.action = #selector(runTapped)

        stopButton = NSButton(frame: NSRect(x: 200, y: 56, width: 156, height: 36))
        stopButton.title = "停止"
        stopButton.bezelStyle = .rounded
        stopButton.target = self
        stopButton.action = #selector(stopTapped)

        content.addSubview(title)
        content.addSubview(statusField)
        content.addSubview(runButton)
        content.addSubview(stopButton)
        content.addSubview(note)
    }

    private func makeLabel(_ text: String, font: NSFont, frame: NSRect) -> NSTextField {
        let field = NSTextField(frame: frame)
        field.stringValue = text
        field.font = font
        field.isBezeled = false
        field.isEditable = false
        field.drawsBackground = false
        return field
    }

    private func setBusy(_ value: Bool, allowStop: Bool = true) {
        busy = value
        runButton.isEnabled = !value
        stopButton.isEnabled = allowStop || !value
    }

    private func refreshStatus() {
        if busy { return }
        statusField.stringValue = isHealthy() ? "状态：运行中" : "状态：未启动"
    }

    @objc private func runTapped() {
        runScript("scripts/launch_dashboard.sh", pending: "状态：正在打开页面…")
    }

    @objc private func stopTapped() {
        runScript("scripts/stop_dashboard.sh", pending: "状态：停止中…", isStop: true)
    }

    private func runScript(_ relative: String, pending: String, isStop: Bool = false) {
        if busy && !isStop { return }
        setBusy(true, allowStop: !isStop)
        statusField.stringValue = pending
        let root = projectRoot()
        DispatchQueue.global(qos: .userInitiated).async {
            let result = runCommand("/bin/bash", arguments: ["\(root)/\(relative)"], directory: root)
            DispatchQueue.main.async {
                if result.code == 0 {
                    if isStop {
                        self.statusField.stringValue = "状态：已停止"
                    } else {
                        self.statusField.stringValue = isHealthy() ? "状态：运行中" : "状态：未启动"
                    }
                } else {
                    self.statusField.stringValue = statusFromFailure(result.output)
                }
                self.setBusy(false)
            }
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
