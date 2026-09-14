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
        if values["gateway_credential_store"] as? String == "keychain" { return true }
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
            if values["gateway_credential_store"] as? String == "keychain" {
                throw BridgeError(message: "This key uses macOS Keychain. Update it with hermes-bridge-tool configure; the app will not create a plaintext copy.")
            }
            try Self.privateWrite(Data(Self.validatedKey(newKey).utf8), to: keyURL)
        }
        try Self.privateWrite(try JSONSerialization.data(withJSONObject: values, options: [.prettyPrinted, .sortedKeys]), to: url)
    }
}

// All connection mutations go through the same CLI manager used by MCP clients.
// The menu bar app never starts or terminates an SSH process itself.
enum BridgeAction: String {
    case connect, reconnect, disconnect, check, lockBridge
    var arguments: [String] {
        switch self {
        case .check: return ["connection-status"]
        case .lockBridge: return ["security", "lock"]
        default: return [rawValue]
        }
    }
    var progress: String {
        switch self {
        case .connect: return "Connecting shared bridge…"
        case .reconnect: return "Reconnecting shared bridge…"
        case .disconnect: return "Disconnecting shared tunnel…"
        case .check: return "Checking configured APIs…"
        case .lockBridge: return "Locking bridge access…"
        }
    }
}

struct BridgeCommandResult {
    let exitCode: Int32
    let ready: Bool
    let locked: Bool?
    let securityMode: String?
    init(data: Data, exitCode: Int32) {
        self.exitCode = exitCode
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        let security = object?["security"] as? [String: Any] ?? object
        if let flag = security?["locked"] as? NSNumber, CFGetTypeID(flag) == CFBooleanGetTypeID() {
            locked = flag.boolValue
        } else { locked = nil }
        let mode = security?["mode"] as? String
        securityMode = mode == "monitor" || mode == "control" ? mode : nil
        if let flag = object?["ready"] as? NSNumber, CFGetTypeID(flag) == CFBooleanGetTypeID() {
            ready = exitCode == 0 && flag.boolValue && locked != true
        } else { ready = false }
    }
}

enum ConnectionIndicator {
    case disconnected, connected, lost

    var color: NSColor {
        switch self {
        case .disconnected: return .systemRed
        case .connected: return .systemGreen
        case .lost: return .systemYellow
        }
    }

    mutating func observe(ready: Bool, reset: Bool = false) {
        if reset { self = .disconnected }
        else if ready { self = .connected }
        else if self == .connected { self = .lost }
    }
}

struct BridgeCLI {
    static func executable() -> URL? {
        let environment = ProcessInfo.processInfo.environment
        let bin = environment["UV_TOOL_BIN_DIR"] ?? environment["XDG_BIN_HOME"] ?? "~/.local/bin"
        let candidates = ["\(bin)/hermes-bridge-tool", "~/.local/bin/hermes-bridge-tool", "~/.local/share/uv/tools/hermes-bridge-tool/bin/hermes-bridge-tool",
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

    static func updateProcess(executable: URL, arguments: [String], output: Pipe) -> Process {
        let process = Process()
        process.executableURL = executable
        process.arguments = ["update"] + arguments
        var environment = ProcessInfo.processInfo.environment
        // Finder-launched apps don't inherit a login shell's Homebrew/user PATH.
        environment["PATH"] = [environment["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin",
                               BridgeConfig.expandedURL("~/.local/bin").path,
                               "/opt/homebrew/bin", "/usr/local/bin"].joined(separator: ":")
        process.environment = environment
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = output
        process.standardError = output
        return process
    }
}

struct BridgeUpdate: Decodable {
    let currentVersion: String
    let latestVersion: String?
    let updateAvailable: Bool
    let tag: String?
    let releaseURL: String

    enum CodingKeys: String, CodingKey {
        case currentVersion = "current_version", latestVersion = "latest_version"
        case updateAvailable = "update_available", tag, releaseURL = "release_url"
    }
}

enum AppUpdates {
    static var version: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Unknown" }
    static var build: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "Unknown" }
    static let automaticKey = "automaticallyCheckForUpdates"
    static let lastCheckKey = "lastUpdateCheck"

    static func shouldCheck(defaults: UserDefaults = .standard, now: Date = Date()) -> Bool {
        guard defaults.bool(forKey: automaticKey) else { return false }
        guard let previous = defaults.object(forKey: lastCheckKey) as? Date else { return true }
        return now.timeIntervalSince(previous) >= 24 * 60 * 60 || previous > now
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let statusLine = NSMenuItem(title: "Disconnected", action: nil, keyEquivalent: "")
    private var connectItem: NSMenuItem!
    private var disconnectItem: NSMenuItem!
    private var reconnectItem: NSMenuItem!
    private var lockItem: NSMenuItem!
    private var connectionProcess: Process?
    private var generation = UUID()
    private var timer: Timer?
    private var healthProcess: Process?
    private var indicator = ConnectionIndicator.disconnected
    private lazy var statusIcon = brandImage()
    private var connected: Bool { indicator == .connected }
    private var locked = false
    private var securityMode = "monitor"
    private var config = BridgeConfig()
    private var settingsWindow: NSWindow?
    private let hostField = NSTextField()
    private let localField = NSTextField()
    private let remoteField = NSTextField()
    private let keyField = NSSecureTextField()
    private let settingsMessage = NSTextField(wrappingLabelWithString: "")
    private var updateItem: NSMenuItem!
    private var updateTimer: Timer?
    private var updateProcess: Process?
    private var installingUpdate = false
    private var restartRequired = false
    private let updateMessage = NSTextField(wrappingLabelWithString: "Check for the latest release on GitHub.")
    private lazy var updateButton = NSButton(title: "Check for Updates…", target: self, action: #selector(checkForUpdates))
    private lazy var automaticUpdates = NSButton(checkboxWithTitle: "Automatically check for updates daily", target: self,
                                                action: #selector(toggleAutomaticUpdates))

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
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
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
        lockItem = menu.addItem(withTitle: "Lock bridge access", action: #selector(lockBridge), keyEquivalent: "")
        lockItem.toolTip = "Blocks new bridge requests and reconnection. Remote work continues."
        menu.addItem(withTitle: "Security and unlocking…", action: #selector(showSecurity), keyEquivalent: "")
        menu.addItem(withTitle: "Set up connection…", action: #selector(setupConnection), keyEquivalent: "")
        menu.addItem(withTitle: "Settings…", action: #selector(showSettings), keyEquivalent: ",")
        updateItem = menu.addItem(withTitle: "Check for Updates…", action: #selector(checkForUpdates), keyEquivalent: "")
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
        UserDefaults.standard.register(defaults: [AppUpdates.automaticKey: true])
        updateTimer = Timer.scheduledTimer(withTimeInterval: 60 * 60, repeats: true) { [weak self] _ in
            self?.checkForAutomaticUpdates()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) { [weak self] in self?.checkForAutomaticUpdates() }
    }

    private func setStatus(_ text: String) {
        statusLine.title = text
        statusLine.toolTip = text
        // Tint the bridge itself to keep status signaling inside one icon.
        if let source = statusIcon {
            let color = indicator.color
            let image = NSImage(size: source.size, flipped: false) { rect in
                source.draw(in: rect)
                color.setFill()
                rect.fill(using: .sourceAtop)
                return true
            }
            image.isTemplate = false
            statusItem.button?.image = image
        }
        statusItem.button?.title = ""
        statusItem.button?.toolTip = text
        statusItem.button?.setAccessibilityValue(text)
    }

    private func showUnavailable(_ text: String, reset: Bool = false) {
        indicator.observe(ready: false, reset: reset)
        updateConnectionButtons()
        setStatus(text)
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
        connectItem?.isEnabled = idle && !connected && !locked
        reconnectItem?.isEnabled = idle && !locked
        disconnectItem?.isEnabled = idle && config.needsTunnel
        lockItem?.isEnabled = idle && !locked
    }

    @objc private func connect() { runConnectionAction(.connect) }
    @objc private func reconnect() { runConnectionAction(.reconnect) }
    @objc private func disconnect() { runConnectionAction(.disconnect) }
    @objc private func lockBridge() { runConnectionAction(.lockBridge) }

    @objc private func showSecurity() {
        let alert = NSAlert()
        alert.messageText = locked ? "Bridge access is locked" : "Bridge access policy"
        alert.informativeText = "Monitoring is the default. Task control and platform messaging need separate local permission. Remote approval responses are disabled.\n\nTo unlock, run in your own Terminal:\n~/.local/bin/hermes-bridge-tool security unlock\n\nTo enable task control:\n~/.local/bin/hermes-bridge-tool security mode control\n\nReview the prompt and type ENABLE yourself. Locking blocks future bridge access; it does not stop remote work or revoke credentials."
        alert.runModal()
    }

    private func runConnectionAction(_ action: BridgeAction) {
        guard connectionProcess == nil else { return }
        if action != .lockBridge {
            do { config = try BridgeConfig.load() }
            catch { showUnavailable(error.localizedDescription); return }
        }
        guard let executable = BridgeCLI.executable() else {
            showUnavailable("Install the CLI to manage the shared connection."); return
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
            showUnavailable("Could not start hermes-bridge-tool \(action.rawValue).")
            return
        }
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            let result = BridgeCommandResult(data: data, exitCode: process.terminationStatus)
            DispatchQueue.main.async {
                guard let self, self.generation == token else { return }
                self.connectionProcess = nil
                if let locked = result.locked { self.locked = locked }
                if let mode = result.securityMode { self.securityMode = mode }
                self.indicator.observe(ready: result.ready, reset: self.locked || (action == .disconnect && result.exitCode == 0))
                self.updateConnectionButtons()
                if self.locked {
                    self.setStatus("Locked · new bridge access blocked · remote work continues")
                } else if action == .disconnect && result.exitCode == 0 {
                    self.setStatus("Shared tunnel disconnected · affects coding clients")
                } else if result.ready {
                    self.showReadyStatus()
                } else {
                    self.setStatus("Connection needs attention · run hermes-bridge-tool \(action.arguments.joined(separator: " ")) for details")
                }
            }
        }
    }

    private func showReadyStatus() {
        setStatus("Ready · \(securityMode == "control" ? "task control enabled" : "monitor only") · \(config.needsTunnel ? "shared SSH" : "direct")")
    }

    private func checkHealth() {
        guard healthProcess == nil, connectionProcess == nil else { return }
        do { config = try BridgeConfig.load() }
        catch { showUnavailable(error.localizedDescription); return }
        guard FileManager.default.fileExists(atPath: BridgeConfig.configURL.path) else {
            showUnavailable("Set up your connection", reset: true)
            return
        }
        let token = generation
        guard let executable = BridgeCLI.executable() else {
            showUnavailable("Install the CLI to check configured backends."); return
        }
        let output = Pipe()
        let process = BridgeCLI.process(executable: executable, action: .check, output: output)
        healthProcess = process
        do { try process.run() }
        catch { healthProcess = nil; showUnavailable("Could not start the connection check."); return }
        // This only observes API health; recovery requires Connect/Reconnect or an agent tool.
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            let result = BridgeCommandResult(data: data, exitCode: process.terminationStatus)
            DispatchQueue.main.async {
                guard let self, self.generation == token else { return }
                self.healthProcess = nil
                if let locked = result.locked { self.locked = locked }
                if let mode = result.securityMode { self.securityMode = mode }
                self.indicator.observe(ready: result.ready, reset: self.locked)
                self.updateConnectionButtons()
                if self.locked { self.setStatus("Locked · new bridge access blocked · remote work continues") }
                else if result.ready { self.showReadyStatus() }
                else { self.setStatus("API check failed · use Reconnect or run hermes-bridge-tool connection-status") }
            }
        }
    }

    private func stopMonitoring() {
        generation = UUID()
        timer?.invalidate(); timer = nil
        updateTimer?.invalidate(); updateTimer = nil
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
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 450, height: 545),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Hermes Bridge Tool Settings"
        window.isReleasedWhenClosed = false
        let content = window.contentView!
        let heading = NSTextField(labelWithString: "Gateway SSH connection")
        heading.font = .boldSystemFont(ofSize: 17)
        heading.frame = NSRect(x: 62, y: 502, width: 365, height: 25)
        if let iconURL = Bundle.main.url(forResource: "app-icon", withExtension: "png"),
           let icon = NSImage(contentsOf: iconURL) {
            let imageView = NSImageView(frame: NSRect(x: 19, y: 497, width: 36, height: 36))
            imageView.image = icon
            content.addSubview(imageView)
        }
        content.addSubview(heading)
        let rows: [(String, NSTextField)] = [("SSH host", hostField), ("Local port", localField),
                                           ("Remote API port", remoteField), ("API key", keyField)]
        for (index, row) in rows.enumerated() {
            let y = 452 - index * 40
            let label = NSTextField(labelWithString: row.0)
            label.frame = NSRect(x: 24, y: y + 3, width: 125, height: 22)
            row.1.frame = NSRect(x: 155, y: y, width: 270, height: 26)
            content.addSubview(label); content.addSubview(row.1)
        }
        keyField.placeholderString = "Leave blank to keep saved key"
        let note = NSTextField(wrappingLabelWithString: "For WebUI or direct HTTPS, use CLI configure-webui / configure --url. Those settings are preserved here. Reconnect after changes.")
        note.textColor = .secondaryLabelColor
        note.frame = NSRect(x: 24, y: 275, width: 400, height: 52)
        content.addSubview(note)
        let separator = NSBox(frame: NSRect(x: 24, y: 263, width: 400, height: 1))
        separator.boxType = .separator
        content.addSubview(separator)
        let version = NSTextField(labelWithString: "Version \(AppUpdates.version) (build \(AppUpdates.build))")
        version.font = .boldSystemFont(ofSize: 13)
        version.frame = NSRect(x: 24, y: 227, width: 400, height: 22)
        content.addSubview(version)
        automaticUpdates.state = UserDefaults.standard.bool(forKey: AppUpdates.automaticKey) ? .on : .off
        automaticUpdates.frame = NSRect(x: 22, y: 195, width: 405, height: 24)
        content.addSubview(automaticUpdates)
        updateButton.bezelStyle = .rounded
        updateButton.frame = NSRect(x: 19, y: 153, width: 190, height: 32)
        content.addSubview(updateButton)
        updateMessage.textColor = .secondaryLabelColor
        updateMessage.frame = NSRect(x: 24, y: 97, width: 400, height: 48)
        content.addSubview(updateMessage)
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

    private func setUpdateStatus(_ text: String, busy: Bool = false) {
        updateMessage.stringValue = text
        updateMessage.toolTip = text
        let title = busy ? (installingUpdate ? "Installing Update…" : "Checking for Updates…")
                         : (restartRequired ? "Restart to Finish Update…" : "Check for Updates…")
        updateButton.title = title
        updateButton.isEnabled = !busy
        updateItem.title = title
        updateItem.isEnabled = !busy
    }

    @objc private func toggleAutomaticUpdates() {
        UserDefaults.standard.set(automaticUpdates.state == .on, forKey: AppUpdates.automaticKey)
        if automaticUpdates.state == .on { checkForAutomaticUpdates() }
    }

    private func checkForAutomaticUpdates() {
        guard !restartRequired, AppUpdates.shouldCheck() else { return }
        checkUpdates(manual: false)
    }

    @objc private func checkForUpdates() {
        if restartRequired { offerRestart(); return }
        checkUpdates(manual: true)
    }

    private func runUpdater(arguments: [String], completion: @escaping (Int32, Data) -> Void) {
        guard let executable = BridgeCLI.executable() else {
            completion(1, Data("Install the matching Hermes Bridge Tool CLI and app using the release installer first.".utf8))
            return
        }
        let output = Pipe()
        let process = BridgeCLI.updateProcess(executable: executable, arguments: arguments, output: output)
        updateProcess = process
        do { try process.run() }
        catch {
            updateProcess = nil
            completion(1, Data("Could not start the updater: \(error.localizedDescription)".utf8))
            return
        }
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            DispatchQueue.main.async {
                guard let self else { return }
                self.updateProcess = nil
                completion(process.terminationStatus, data)
            }
        }
    }

    private func checkUpdates(manual: Bool) {
        guard updateProcess == nil, !installingUpdate else { return }
        UserDefaults.standard.set(Date(), forKey: AppUpdates.lastCheckKey)
        setUpdateStatus("Checking GitHub releases…", busy: true)
        runUpdater(arguments: ["--check", "--json", "--current-version", AppUpdates.version]) { [weak self] code, data in
            guard let self else { return }
            guard code == 0, let update = try? JSONDecoder().decode(BridgeUpdate.self, from: data) else {
                self.setUpdateStatus("Could not check for updates. Try again using Check for Updates.")
                if manual { self.showUpdateError(title: "Could not check for updates", data: data) }
                return
            }
            if update.updateAvailable {
                guard let latest = update.latestVersion, let tag = update.tag else {
                    self.setUpdateStatus("The release response was incomplete. Try checking again later.")
                    return
                }
                self.setUpdateStatus("Version \(latest) is available.")
                self.offerUpdate(version: latest, tag: tag)
            } else {
                let message = update.latestVersion.map { "You’re up to date. Latest release: \($0)." }
                    ?? "No published stable release is available yet."
                self.setUpdateStatus(message)
                if manual {
                    let alert = NSAlert()
                    alert.messageText = update.latestVersion == nil ? "No releases available" : "You’re up to date"
                    alert.informativeText = "Hermes Bridge Tool \(AppUpdates.version) is installed. \(message)"
                    NSApp.activate(ignoringOtherApps: true)
                    alert.runModal()
                }
            }
        }
    }

    private func offerUpdate(version: String, tag: String) {
        let alert = NSAlert()
        alert.messageText = "Hermes Bridge Tool \(version) is available"
        alert.informativeText = "You have version \(AppUpdates.version). Download and install this release? Your connection settings and credentials will be preserved.\n\nGitHub CLI (gh) is required to verify the download. You’ll be asked to restart the app after installation."
        alert.addButton(withTitle: "Install Update")
        alert.addButton(withTitle: "Later")
        alert.addButton(withTitle: "Release Notes")
        NSApp.activate(ignoringOtherApps: true)
        let response = alert.runModal()
        if response == .alertThirdButtonReturn {
            // Construct the official link rather than opening a URL from remote metadata.
            if let tag = tag.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed),
               let url = URL(string: "https://github.com/hourafter4/hermes-bridge-tool/releases/tag/\(tag)") {
                NSWorkspace.shared.open(url)
            }
            return
        }
        guard response == .alertFirstButtonReturn else { return }
        installingUpdate = true
        setUpdateStatus("Downloading, verifying, and installing \(version)… This may take a few minutes.", busy: true)
        if settingsWindow?.isVisible != true { showSettings() }
        runUpdater(arguments: ["--yes", "--tag", tag, "--current-version", AppUpdates.version]) { [weak self] code, data in
            guard let self else { return }
            self.installingUpdate = false
            guard code == 0 else {
                self.setUpdateStatus("Update failed. Use Check for Updates to retry.")
                self.showUpdateError(title: "Could not install the update", data: data)
                return
            }
            self.restartRequired = true
            self.setUpdateStatus("Version \(version) installed. Restart the app to finish.")
            self.offerRestart()
        }
    }

    private func showUpdateError(title: String, data: Data) {
        let alert = NSAlert()
        alert.messageText = title
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        let details = object?["error"] as? String ?? String(data: data, encoding: .utf8) ?? ""
        alert.informativeText = details.isEmpty ? "Try again later, or download the release from GitHub."
                                               : String(details.suffix(3000))
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    private func offerRestart() {
        let alert = NSAlert()
        alert.messageText = "Update installed"
        alert.informativeText = "Restart Hermes Bridge Tool to use the new version. Your shared connection will stay running.\n\nRun hermes-bridge-tool register both (or codex / claude), then restart your coding clients to load the updated CLI."
        alert.addButton(withTitle: "Restart Now")
        alert.addButton(withTitle: "Later")
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let process = Process()
        process.executableURL = BridgeConfig.expandedURL("~/Applications/Hermes Bridge Tool.app/Contents/MacOS/HermesBridgeTool")
        process.arguments = ["--wait-for-exit", String(ProcessInfo.processInfo.processIdentifier)]
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do { try process.run(); NSApp.terminate(nil) }
        catch { showUpdateError(title: "Could not restart the app", data: Data("Quit and reopen Hermes Bridge Tool from ~/Applications. \(error.localizedDescription)".utf8)) }
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
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard installingUpdate else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "Update in progress"
        alert.informativeText = "Wait for installation to finish before quitting Hermes Bridge Tool."
        alert.runModal()
        return .terminateCancel
    }
    func applicationWillTerminate(_ notification: Notification) { stopMonitoring() }
}

func selfTest() throws {
    let updateData = Data(#"{"current_version":"0.2.1","latest_version":"0.3.0","update_available":true,"tag":"v0.3.0","release_url":"https://github.com/hourafter4/hermes-bridge-tool/releases/tag/v0.3.0"}"#.utf8)
    let update = try JSONDecoder().decode(BridgeUpdate.self, from: updateData)
    precondition(update.updateAvailable && update.latestVersion == "0.3.0" && update.tag == "v0.3.0")
    let noRelease = try JSONDecoder().decode(BridgeUpdate.self, from: Data(#"{"current_version":"0.2.1","latest_version":null,"update_available":false,"tag":null,"release_url":"https://github.com/hourafter4/hermes-bridge-tool/releases"}"#.utf8))
    precondition(!noRelease.updateAvailable && noRelease.latestVersion == nil && noRelease.tag == nil)
    let preferencesID = "hermes-update-test-\(UUID().uuidString)"
    let preferences = UserDefaults(suiteName: preferencesID)!
    defer { preferences.removePersistentDomain(forName: preferencesID) }
    preferences.register(defaults: [AppUpdates.automaticKey: true])
    let now = Date()
    precondition(AppUpdates.shouldCheck(defaults: preferences, now: now))
    preferences.set(now, forKey: AppUpdates.lastCheckKey)
    precondition(!AppUpdates.shouldCheck(defaults: preferences, now: now.addingTimeInterval(3600)))
    precondition(AppUpdates.shouldCheck(defaults: preferences, now: now.addingTimeInterval(86400)))
    preferences.set(false, forKey: AppUpdates.automaticKey)
    precondition(!AppUpdates.shouldCheck(defaults: preferences, now: now.addingTimeInterval(86400)))
    preferences.set(true, forKey: AppUpdates.automaticKey)
    precondition(AppUpdates.shouldCheck(defaults: preferences, now: now.addingTimeInterval(-1)))
    let updateProcess = BridgeCLI.updateProcess(executable: URL(fileURLWithPath: "/test/hermes-bridge-tool"),
                                              arguments: ["--yes", "--tag", "v0.3.0"], output: Pipe())
    precondition(updateProcess.arguments == ["update", "--yes", "--tag", "v0.3.0"])
    precondition(updateProcess.environment?["PATH"]?.contains("/opt/homebrew/bin") == true)
    // Repeated failures distinguish a lost connection from a deliberate stop.
    var indicator = ConnectionIndicator.disconnected
    indicator.observe(ready: false)
    precondition(indicator == .disconnected)
    indicator.observe(ready: true)
    precondition(indicator == .connected)
    indicator.observe(ready: false)
    indicator.observe(ready: false)
    precondition(indicator == .lost)
    indicator.observe(ready: true)
    precondition(indicator == .connected)
    indicator.observe(ready: false, reset: true)
    indicator.observe(ready: false)
    precondition(indicator == .disconnected)
    indicator.observe(ready: true, reset: true)
    precondition(indicator == .disconnected)
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
    var keychainValues = webuiOnly.values
    keychainValues["gateway_credential_store"] = "keychain"
    let keychain = BridgeConfig(values: keychainValues, environment: [:])
    precondition(keychain.gatewayConfigured && keychain.gatewayUsesSSH)
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
    precondition(BridgeAction.check.arguments == ["connection-status"])
    let lockProcess = BridgeCLI.process(executable: URL(fileURLWithPath: "/test/hermes-bridge-tool"), action: .lockBridge, output: Pipe())
    precondition(lockProcess.arguments == ["security", "lock"])
    precondition(BridgeCommandResult(data: Data(#"{"ready":true}"#.utf8), exitCode: 0).ready)
    precondition(!BridgeCommandResult(data: Data(#"{"ready":true}"#.utf8), exitCode: 1).ready)
    for data in [#"{"ready":false}"#, #"{"ready":1}"#, #"{"ready":"true"}"#, "not JSON", "{}"] {
        precondition(!BridgeCommandResult(data: Data(data.utf8), exitCode: 0).ready)
    }
    let locked = BridgeCommandResult(data: Data(#"{"ready":true,"security":{"locked":true,"mode":"control"}}"#.utf8), exitCode: 0)
    precondition(locked.locked == true && !locked.ready && locked.securityMode == "control")
    let lockResult = BridgeCommandResult(data: Data(#"{"locked":true,"mode":"monitor"}"#.utf8), exitCode: 0)
    precondition(lockResult.locked == true && !lockResult.ready && lockResult.securityMode == "monitor")
    for raw in [#"{"locked":1}"#, #"{"locked":"true"}"#, #"{"security":{"locked":0,"mode":"invalid"}}"#] {
        let invalid = BridgeCommandResult(data: Data(raw.utf8), exitCode: 0)
        precondition(invalid.locked == nil && invalid.securityMode == nil)
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
    do {
        try BridgeConfig(values: ["gateway_credential_store": "keychain", "api_key_file": keyURL.path]).save(to: configURL, newKey: "must-not-write")
        fatalError("Keychain key was copied to plaintext")
    } catch is BridgeError {}
    precondition(!FileManager.default.fileExists(atPath: keyURL.path))
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
    print("Hermes Bridge Tool: config, private storage, backend selection, shared connection commands, security lock, connection indicators, and update checks passed.")
}

if CommandLine.arguments.contains("--self-test") {
    do { try selfTest() }
    catch { fputs("Self-test failed: \(error.localizedDescription)\n", stderr); exit(1) }
} else {
    // The replacement app waits for the old instance to leave before enforcing
    // the single-instance rule. No shell or persistent helper is required.
    if let index = CommandLine.arguments.firstIndex(of: "--wait-for-exit") {
        guard CommandLine.arguments.indices.contains(index + 1),
              let oldPID = Int32(CommandLine.arguments[index + 1]), oldPID > 1 else { exit(1) }
        let deadline = Date().addingTimeInterval(30)
        while kill(oldPID, 0) == 0 && Date() < deadline { usleep(100_000) }
        if kill(oldPID, 0) == 0 { exit(1) }
    }
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.accessory)
    app.run()
}
