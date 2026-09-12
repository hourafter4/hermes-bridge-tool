import AppKit
import Foundation
import Darwin

struct BridgeError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

struct BridgeConfig {
    static let defaultDirectory = "~/.config/hermes-bridge-tool"
    static var configURL: URL {
        expandedURL(ProcessInfo.processInfo.environment["HERMES_BRIDGE_TOOL_CONFIG"] ?? "\(defaultDirectory)/config.json")
    }
    static func expandedURL(_ path: String) -> URL {
        URL(fileURLWithPath: (path as NSString).expandingTildeInPath)
    }
    var values: [String: Any] = [:]
    var environment: [String: String] = ProcessInfo.processInfo.environment
    var host: String { values["ssh_host"] as? String ?? "hermes-server" }
    var localPort: Int { values["local_port"] as? Int ?? 18642 }
    var remotePort: Int { values["remote_port"] as? Int ?? 8642 }
    var gatewayURL: String { values["gateway_url"] as? String ?? "" }
    var webuiURL: String { values["webui_url"] as? String ?? "" }
    var webuiSSH: Bool { values["webui_ssh"] as? Bool ?? false }
    var webuiLocalPort: Int { values["webui_local_port"] as? Int ?? 18787 }
    var webuiRemotePort: Int { values["webui_remote_port"] as? Int ?? 8787 }
    var gatewayTransportUsesSSH: Bool {
        gatewayURL.isEmpty || Self.matchesForward(gatewayURL, port: localPort)
    }
    var webuiConfigured: Bool {
        !(environment["HERMES_WEBUI_URL"] ?? webuiURL).isEmpty || webuiSSH
    }
    var gatewayConfigured: Bool {
        if environment["HERMES_API_KEY"] != nil { return true }
        let keyPath = environment["HERMES_API_KEY_FILE"].map { Self.expandedURL($0) } ?? keyURL
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(atPath: keyPath.path, isDirectory: &isDirectory) && !isDirectory.boolValue
    }
    var gatewayUsesSSH: Bool {
        gatewayTransportUsesSSH && (!webuiConfigured || gatewayConfigured)
    }
    var needsTunnel: Bool { gatewayUsesSSH || webuiSSH }
    var keyURL: URL { Self.expandedURL(values["api_key_file"] as? String ?? "\(Self.defaultDirectory)/api-key") }

    static func load(from url: URL = configURL) throws -> BridgeConfig {
        guard FileManager.default.fileExists(atPath: url.path) else { return BridgeConfig() }
        guard let values = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any] else {
            throw BridgeError(message: "The config file must contain a JSON object.")
        }
        let config = BridgeConfig(values: values)
        try config.validate()
        return config
    }
    func validate() throws {
        if let value = values["ssh_host"], !(value is String) {
            throw BridgeError(message: "SSH host must be a string.")
        }
        guard host.range(of: "^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,254}$", options: .regularExpression) == host.startIndex..<host.endIndex else {
            throw BridgeError(message: "Use an SSH alias or user@hostname, without spaces or options.")
        }
        for name in ["local_port", "remote_port", "webui_local_port", "webui_remote_port"] {
            if let value = values[name] {
                guard let number = value as? NSNumber,
                      CFGetTypeID(number) != CFBooleanGetTypeID(),
                      !["f", "d"].contains(String(cString: number.objCType)),
                      number.doubleValue == Double(number.intValue),
                      (1...65535).contains(number.intValue) else {
                    throw BridgeError(message: "Ports must be integers between 1 and 65535.")
                }
            }
        }
        for name in ["api_key_file", "webui_auth_file"] {
            if let value = values[name] {
                guard let path = value as? String, !path.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    throw BridgeError(message: "\(name) must be a nonempty path.")
                }
            }
        }
        for name in ["gateway_url", "webui_url"] {
            if let value = values[name] {
                guard let url = value as? String else {
                    throw BridgeError(message: "\(name) must be a URL string.")
                }
                if !url.isEmpty { try Self.validateEndpoint(url, label: name) }
            }
        }
        if let value = values["webui_ssh"] {
            guard let flag = value as? NSNumber, CFGetTypeID(flag) == CFBooleanGetTypeID() else {
                throw BridgeError(message: "webui_ssh must be a boolean.")
            }
        }
        if webuiSSH {
            let url = webuiURL.isEmpty ? "http://127.0.0.1:\(webuiLocalPort)" : webuiURL
            guard Self.matchesForward(url, port: webuiLocalPort) else {
                throw BridgeError(message: "WebUI SSH requires a loopback URL using webui_local_port.")
            }
            if gatewayTransportUsesSSH && localPort == webuiLocalPort {
                throw BridgeError(message: "Gateway and WebUI SSH forwards must use different local ports.")
            }
        }
    }
    static func matchesForward(_ raw: String, port: Int) -> Bool {
        guard let url = URLComponents(string: raw) else { return false }
        return ["127.0.0.1", "localhost", "::1", "[::1]"].contains(url.host?.lowercased() ?? "") && url.port == port
    }
    static func validateEndpoint(_ raw: String, label: String) throws {
        let error = BridgeError(message: "\(label) must use HTTPS or loopback HTTP, without URL credentials, query, or fragment.")
        guard !raw.unicodeScalars.contains(where: { CharacterSet.whitespacesAndNewlines.contains($0) || $0.value < 32 }),
              !raw.contains("?"), !raw.contains("#"), !raw.contains("\\"),
              let url = URLComponents(string: raw), let host = url.host, !host.isEmpty,
              url.user == nil, url.password == nil else { throw error }
        let scheme = url.scheme?.lowercased()
        let loopback = ["127.0.0.1", "localhost", "::1", "[::1]"].contains(host.lowercased())
        guard scheme == "https" || (scheme == "http" && loopback),
              url.port == nil || (1...65535).contains(url.port!) else { throw error }
    }
    func readKey() throws -> String {
        try Self.validatedKey(String(contentsOf: keyURL, encoding: .utf8))
    }
    static func validatedKey(_ raw: String) throws -> String {
        let key = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty, key.unicodeScalars.allSatisfy({ (33...126).contains($0.value) }) else {
            throw BridgeError(message: "The API key must be a nonempty ASCII token without whitespace.")
        }
        return key
    }
    static func privateWrite(_ data: Data, to url: URL) throws {
        let directory = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                              attributes: [.posixPermissions: 0o700])
        let temporary = directory.appendingPathComponent(".hermes-\(UUID().uuidString)")
        guard FileManager.default.createFile(atPath: temporary.path, contents: nil,
                                             attributes: [.posixPermissions: 0o600]) else {
            throw BridgeError(message: "Could not create a private settings file.")
        }
        defer { try? FileManager.default.removeItem(at: temporary) }
        let handle = try FileHandle(forWritingTo: temporary)
        do { try handle.write(contentsOf: data); try handle.close() }
        catch { try? handle.close(); throw error }
        guard rename(temporary.path, url.path) == 0 else {
            throw BridgeError(message: "Could not save settings: \(String(cString: strerror(errno)))")
        }
    }
    func save(to url: URL = configURL, newKey: String = "") throws {
        try validate()
        if !newKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            try Self.privateWrite(Data(Self.validatedKey(newKey).utf8), to: keyURL)
        }
        try Self.privateWrite(try JSONSerialization.data(withJSONObject: values, options: [.prettyPrinted, .sortedKeys]), to: url)
    }
}

// All connection mutations go through the same CLI manager used by MCP clients.
// The menu bar app never starts or terminates an SSH process itself.
enum BridgeAction: String {
    case connect, reconnect, disconnect, check
    var arguments: [String] { self == .check ? ["doctor", "--backend", "all"] : [rawValue] }
    var progress: String {
        switch self {
        case .connect: return "Connecting shared bridge…"
        case .reconnect: return "Reconnecting shared bridge…"
        case .disconnect: return "Disconnecting shared tunnel…"
        case .check: return "Checking configured APIs…"
        }
    }
}

struct BridgeCommandResult {
    let exitCode: Int32
    let ready: Bool
    init(data: Data, exitCode: Int32) {
        self.exitCode = exitCode
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        if let flag = object?["ready"] as? NSNumber, CFGetTypeID(flag) == CFBooleanGetTypeID() {
            ready = exitCode == 0 && flag.boolValue
        } else { ready = false }
    }
}

struct BridgeCLI {
    static func executable() -> URL? {
        let candidates = ["~/.local/bin/hermes-bridge-tool", "~/.local/share/uv/tools/hermes-bridge-tool/bin/hermes-bridge-tool",
                          "/opt/homebrew/bin/hermes-bridge-tool", "/usr/local/bin/hermes-bridge-tool"]
        return candidates.map { BridgeConfig.expandedURL($0) }.first {
            FileManager.default.isExecutableFile(atPath: $0.path)
        }
    }
    static func process(executable: URL, action: BridgeAction, output: Pipe) -> Process {
        let process = Process()
        process.executableURL = executable
        process.arguments = action.arguments
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        return process
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let statusLine = NSMenuItem(title: "Disconnected", action: nil, keyEquivalent: "")
    private var connectItem: NSMenuItem!
    private var disconnectItem: NSMenuItem!
    private var reconnectItem: NSMenuItem!
    private var connectionProcess: Process?
    private var generation = UUID()
    private var timer: Timer?
    private var healthProcess: Process?
    private var connected = false
    private var config = BridgeConfig()
    private var settingsWindow: NSWindow?
    private let hostField = NSTextField()
    private let localField = NSTextField()
    private let remoteField = NSTextField()
    private let keyField = NSSecureTextField()
    private let settingsMessage = NSTextField(wrappingLabelWithString: "")

    private func brandImage() -> NSImage? {
        guard let url = Bundle.main.url(forResource: "menu-bar-template", withExtension: "png"),
              let image = NSImage(contentsOf: url) else {
            return NSImage(systemSymbolName: "link", accessibilityDescription: "Hermes Bridge Tool")
        }
        if let retinaURL = Bundle.main.url(forResource: "menu-bar-template@2x", withExtension: "png"),
           let data = try? Data(contentsOf: retinaURL),
           let retina = NSBitmapImageRep(data: data) {
            retina.size = NSSize(width: 18, height: 18)
            image.addRepresentation(retina)
        }
        image.size = NSSize(width: 18, height: 18)
        image.isTemplate = true
        return image
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // A manually executed second binary must not create a second menu bar icon.
        if let bundleID = Bundle.main.bundleIdentifier,
           NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
            .contains(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }) {
            NSApp.terminate(nil)
            return
        }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = brandImage()
        statusItem.button?.imagePosition = .imageOnly
        statusItem.button?.setAccessibilityLabel("Hermes Bridge Tool")
        setStatus("Disconnected")
        let menu = NSMenu()
        menu.addItem(statusLine)
        menu.addItem(.separator())
        connectItem = menu.addItem(withTitle: "Connect", action: #selector(connect), keyEquivalent: "")
        reconnectItem = menu.addItem(withTitle: "Reconnect", action: #selector(reconnect), keyEquivalent: "")
        disconnectItem = menu.addItem(withTitle: "Disconnect shared tunnel", action: #selector(disconnect), keyEquivalent: "")
        disconnectItem.toolTip = "Disconnects the shared SSH tunnel used by the app and coding clients."
        disconnectItem.isEnabled = false
        menu.addItem(withTitle: "Set up connection…", action: #selector(setupConnection), keyEquivalent: "")
        menu.addItem(withTitle: "Settings…", action: #selector(showSettings), keyEquivalent: ",")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit Hermes Bridge Tool", action: #selector(quit), keyEquivalent: "q")
        for item in menu.items where item.action != nil { item.target = self }
        menu.autoenablesItems = false
        statusItem.menu = menu
        do {
            config = try BridgeConfig.load()
            if !FileManager.default.fileExists(atPath: BridgeConfig.configURL.path) {
                setStatus("Set up your connection")
            }
        }
        catch { setStatus(error.localizedDescription) }
        // Read-only polling also reflects connections made by coding agents.
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in self?.checkHealth() }
        checkHealth()
    }

    private func setStatus(_ text: String) {
        statusLine.title = text
        statusLine.toolTip = text
        statusItem.button?.title = ""
        statusItem.button?.toolTip = text
        statusItem.button?.setAccessibilityValue(text)
    }

    @objc private func setupConnection() {
        guard let url = Bundle.main.url(forResource: "setup-terminal", withExtension: "command"),
              NSWorkspace.shared.open(url) else {
            let alert = NSAlert()
            alert.messageText = "Could not open connection setup"
            alert.informativeText = "Run hermes-bridge-tool setup in Terminal, or reinstall the menu bar app."
            alert.runModal()
            return
        }
    }

    private func updateConnectionButtons() {
        let idle = connectionProcess == nil
        connectItem?.isEnabled = idle && !connected
        reconnectItem?.isEnabled = idle
        disconnectItem?.isEnabled = idle && config.needsTunnel
    }

    @objc private func connect() { runConnectionAction(.connect) }
    @objc private func reconnect() { runConnectionAction(.reconnect) }
    @objc private func disconnect() { runConnectionAction(.disconnect) }

    private func runConnectionAction(_ action: BridgeAction) {
        guard connectionProcess == nil else { return }
        do { config = try BridgeConfig.load() }
        catch { setStatus(error.localizedDescription); return }
        guard let executable = BridgeCLI.executable() else {
            setStatus("Install the CLI to manage the shared connection."); return
        }
        generation = UUID()
        let token = generation
        if healthProcess?.isRunning == true { healthProcess?.terminate() }
        healthProcess = nil
        let output = Pipe()
        let process = BridgeCLI.process(executable: executable, action: action, output: output)
        connectionProcess = process
        updateConnectionButtons()
        setStatus(action.progress)
        do { try process.run() }
        catch {
            connectionProcess = nil
            updateConnectionButtons()
            setStatus("Could not start hermes-bridge-tool \(action.rawValue).")
            return
        }
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            let result = BridgeCommandResult(data: data, exitCode: process.terminationStatus)
            DispatchQueue.main.async {
                guard let self, self.generation == token else { return }
                self.connectionProcess = nil
                self.connected = result.ready
                self.updateConnectionButtons()
                if action == .disconnect && result.exitCode == 0 {
                    self.setStatus("Shared tunnel disconnected · affects coding clients")
                } else if result.ready {
                    self.showReadyStatus()
                } else {
                    self.setStatus("Connection needs attention · run hermes-bridge-tool \(action.rawValue) for details")
                }
            }
        }
    }

    private func showReadyStatus() {
        setStatus("Ready · configured APIs · \(config.needsTunnel ? "shared SSH" : "direct")")
    }

    private func checkHealth() {
        guard healthProcess == nil, connectionProcess == nil else { return }
        do { config = try BridgeConfig.load() }
        catch { connected = false; updateConnectionButtons(); setStatus(error.localizedDescription); return }
        guard FileManager.default.fileExists(atPath: BridgeConfig.configURL.path) else {
            connected = false
            updateConnectionButtons()
            setStatus("Set up your connection")
            return
        }
        let token = generation
        guard let executable = BridgeCLI.executable() else {
            setStatus("Install the CLI to check configured backends."); return
        }
        let output = Pipe()
        let process = BridgeCLI.process(executable: executable, action: .check, output: output)
        healthProcess = process
        do { try process.run() }
        catch { healthProcess = nil; setStatus("Could not start the connection check."); return }
        // This only observes API health; recovery requires Connect/Reconnect or an agent tool.
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            let result = BridgeCommandResult(data: data, exitCode: process.terminationStatus)
            DispatchQueue.main.async {
                guard let self, self.generation == token else { return }
                self.healthProcess = nil
                self.connected = result.ready
                self.updateConnectionButtons()
                if result.ready { self.showReadyStatus() }
                else { self.setStatus("API check failed · use Reconnect or run hermes-bridge-tool doctor --backend all") }
            }
        }
    }

    private func stopMonitoring() {
        generation = UUID()
        timer?.invalidate(); timer = nil
        if healthProcess?.isRunning == true { healthProcess?.terminate() }
        healthProcess = nil
        // Leave the shared tunnel and any in-flight connection command alive:
        // coding clients use it independently of this app's lifetime.
    }

    @objc private func showSettings() {
        if settingsWindow == nil { buildSettings() }
        var displayed = config
        do { displayed = try BridgeConfig.load(); settingsMessage.stringValue = "" }
        catch { settingsMessage.stringValue = error.localizedDescription }
        hostField.stringValue = displayed.host
        localField.stringValue = String(displayed.localPort)
        remoteField.stringValue = String(displayed.remotePort)
        keyField.stringValue = ""
        NSApp.activate(ignoringOtherApps: true)
        settingsWindow?.makeKeyAndOrderFront(nil)
    }

    private func buildSettings() {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 450, height: 365),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Hermes Bridge Tool Settings"
        window.isReleasedWhenClosed = false
        let content = window.contentView!
        let heading = NSTextField(labelWithString: "Gateway SSH connection")
        heading.font = .boldSystemFont(ofSize: 17)
        heading.frame = NSRect(x: 62, y: 322, width: 365, height: 25)
        if let iconURL = Bundle.main.url(forResource: "app-icon", withExtension: "png"),
           let icon = NSImage(contentsOf: iconURL) {
            let imageView = NSImageView(frame: NSRect(x: 19, y: 317, width: 36, height: 36))
            imageView.image = icon
            content.addSubview(imageView)
        }
        content.addSubview(heading)
        let rows: [(String, NSTextField)] = [("SSH host", hostField), ("Local port", localField),
                                           ("Remote API port", remoteField), ("API key", keyField)]
        for (index, row) in rows.enumerated() {
            let y = 272 - index * 40
            let label = NSTextField(labelWithString: row.0)
            label.frame = NSRect(x: 24, y: y + 3, width: 125, height: 22)
            row.1.frame = NSRect(x: 155, y: y, width: 270, height: 26)
            content.addSubview(label); content.addSubview(row.1)
        }
        keyField.placeholderString = "Leave blank to keep saved key"
        let note = NSTextField(wrappingLabelWithString: "For WebUI or direct HTTPS, use CLI configure-webui / configure --url. Those settings are preserved here. Reconnect after changes.")
        note.textColor = .secondaryLabelColor
        note.frame = NSRect(x: 24, y: 95, width: 400, height: 52)
        content.addSubview(note)
        settingsMessage.textColor = .systemRed
        settingsMessage.frame = NSRect(x: 24, y: 48, width: 400, height: 42)
        content.addSubview(settingsMessage)
        let save = NSButton(title: "Save", target: self, action: #selector(saveSettings))
        save.bezelStyle = .rounded
        save.keyEquivalent = "\r"
        save.frame = NSRect(x: 335, y: 12, width: 90, height: 32)
        content.addSubview(save)
        let setup = NSButton(title: "Guided setup…", target: self, action: #selector(setupConnection))
        setup.bezelStyle = .rounded
        setup.frame = NSRect(x: 20, y: 12, width: 132, height: 32)
        content.addSubview(setup)
        window.center()
        settingsWindow = window
    }

    @objc private func saveSettings() {
        do {
            var updated = try BridgeConfig.load()
            guard let local = Int(localField.stringValue), let remote = Int(remoteField.stringValue) else {
                throw BridgeError(message: "Ports must be integers between 1 and 65535.")
            }
            updated.values["ssh_host"] = hostField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            updated.values["local_port"] = local
            updated.values["remote_port"] = remote
            try updated.save(newKey: keyField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines))
            if !connected { config = updated }
            keyField.stringValue = ""
            settingsWindow?.orderOut(nil)
        } catch { settingsMessage.stringValue = error.localizedDescription }
    }

    @objc private func quit() { NSApp.terminate(nil) }
    func applicationWillTerminate(_ notification: Notification) { stopMonitoring() }
}

func selfTest() throws {
    let config = BridgeConfig(environment: [:])
    try config.validate()
    precondition(config.host == "hermes-server" && config.needsTunnel)
    let direct = BridgeConfig(values: ["gateway_url": "https://gateway.example.test"], environment: [:])
    precondition(!direct.needsTunnel)
    let missingKey = "/tmp/hermes-missing-key-\(UUID().uuidString)"
    let webuiOnly = BridgeConfig(values: ["webui_ssh": true, "api_key_file": missingKey], environment: [:])
    try webuiOnly.validate()
    precondition(webuiOnly.webuiSSH && webuiOnly.needsTunnel && !webuiOnly.gatewayUsesSSH)
    let both = BridgeConfig(values: webuiOnly.values, environment: ["HERMES_API_KEY": "test-env-key"])
    precondition(both.gatewayUsesSSH && both.webuiSSH)
    let publicWebui = BridgeConfig(values: ["webui_url": "https://webui.example.test/prefix", "api_key_file": missingKey], environment: [:])
    precondition(!publicWebui.needsTunnel)
    let envWebui = BridgeConfig(values: ["api_key_file": missingKey], environment: ["HERMES_WEBUI_URL": "https://webui.example.test"])
    precondition(!envWebui.needsTunnel)
    for url in ["https://gateway.example.test/prefix", "http://127.0.0.1:18642/prefix", "http://[::1]:18787"] {
        try BridgeConfig.validateEndpoint(url, label: "Test URL")
    }
    for action in [BridgeAction.connect, .reconnect, .disconnect] {
        let process = BridgeCLI.process(executable: URL(fileURLWithPath: "/test/hermes-bridge-tool"), action: action, output: Pipe())
        precondition(process.executableURL?.path == "/test/hermes-bridge-tool")
        precondition(process.arguments == [action.rawValue])
    }
    precondition(BridgeAction.check.arguments == ["doctor", "--backend", "all"])
    precondition(BridgeCommandResult(data: Data(#"{"ready":true}"#.utf8), exitCode: 0).ready)
    precondition(!BridgeCommandResult(data: Data(#"{"ready":true}"#.utf8), exitCode: 1).ready)
    for data in [#"{"ready":false}"#, #"{"ready":1}"#, #"{"ready":"true"}"#, "not JSON", "{}"] {
        precondition(!BridgeCommandResult(data: Data(data.utf8), exitCode: 0).ready)
    }
    for values: [String: Any] in [["ssh_host": "-oProxyCommand=bad"], ["ssh_host": "host;bad"],
                                 ["ssh_host": "host\n"], ["local_port": 0], ["remote_port": 65536], ["local_port": true],
                                 ["local_port": "123"], ["remote_port": 1.5], ["remote_port": 8642.0],
                                 ["ssh_host": String(repeating: "a", count: 256)],
                                 ["webui_local_port": true], ["webui_remote_port": 0],
                                 ["webui_auth_file": " "], ["webui_auth_file": 123],
                                 ["webui_ssh": 1], ["webui_ssh": "true"], ["gateway_url": 123],
                                 ["gateway_url": "http://public.example.test"],
                                 ["webui_url": "https://user:password@example.test"],
                                 ["webui_url": "https://example.test/?token=secret"],
                                 ["webui_url": "https://example.test/#fragment"],
                                 ["webui_url": "https://example.test\n"],
                                 ["webui_url": "https://example.test:0"],
                                 ["webui_url": "https://example.test:65536"],
                                 ["webui_ssh": true, "webui_url": "https://example.test"],
                                 ["webui_ssh": true, "webui_url": "http://127.0.0.1:9999"],
                                 ["webui_ssh": true, "local_port": 18787]] {
        do { try BridgeConfig(values: values).validate(); fatalError("Invalid config accepted") }
        catch is BridgeError {}
    }
    for key in ["", "has space", "has\ttab", "unicode-é", "line\nbreak"] {
        do { _ = try BridgeConfig.validatedKey(key); fatalError("Invalid API key accepted") }
        catch is BridgeError {}
    }
    let trimmedKey = try BridgeConfig.validatedKey("  test-key\n")
    precondition(trimmedKey == "test-key")
    let floatPort = try JSONSerialization.jsonObject(with: Data(#"{"local_port":18642.0}"#.utf8)) as! [String: Any]
    do { try BridgeConfig(values: floatPort).validate(); fatalError("JSON float port accepted") }
    catch is BridgeError {}
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent("hermes-test-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: directory) }
    let configURL = directory.appendingPathComponent("config.json")
    let keyURL = directory.appendingPathComponent("api-key")
    let saved = BridgeConfig(values: ["ssh_host": "user@host", "local_port": 1234,
                                     "api_key_file": keyURL.path, "preserved": "yes"])
    try saved.save(to: configURL, newKey: "test-key")
    let loaded = try BridgeConfig.load(from: configURL)
    let fileOverride = BridgeConfig(values: webuiOnly.values, environment: ["HERMES_API_KEY_FILE": keyURL.path])
    precondition(fileOverride.gatewayUsesSSH)
    let loadedKey = try loaded.readKey()
    precondition(loadedKey == "test-key")
    precondition(loaded.values["preserved"] as? String == "yes")
    try loaded.save(to: configURL)
    let preservedKey = try loaded.readKey()
    precondition(preservedKey == "test-key")
    for url in [configURL, keyURL] {
        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        precondition((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600)
    }
    print("Hermes Bridge Tool: config, private storage, backend selection, and shared connection commands passed.")
}

if CommandLine.arguments.contains("--self-test") {
    do { try selfTest() }
    catch { fputs("Self-test failed: \(error.localizedDescription)\n", stderr); exit(1) }
} else {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.accessory)
    app.run()
}
