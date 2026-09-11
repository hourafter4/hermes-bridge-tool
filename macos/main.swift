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
    var host: String { values["ssh_host"] as? String ?? "hetzner" }
    var localPort: Int { values["local_port"] as? Int ?? 18642 }
    var remotePort: Int { values["remote_port"] as? Int ?? 8642 }
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
        for name in ["local_port", "remote_port"] {
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
        if let value = values["api_key_file"] {
            guard let path = value as? String, !path.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw BridgeError(message: "api_key_file must be a nonempty path.")
            }
        }
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
    var sshArguments: [String] {
        ["-N", "-T", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
         "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
         "-o", "StrictHostKeyChecking=yes", "-o", "ControlMaster=no",
         "-o", "ControlPath=none", "-o", "ControlPersist=no", "-o", "ForkAfterAuthentication=no",
         "-L", "127.0.0.1:\(localPort):127.0.0.1:\(remotePort)", host]
    }
}

func capabilitiesReady(_ data: Data) -> Bool {
    guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let features = json["features"] as? [String: Any] else { return false }
    return ["run_submission", "run_status", "run_stop"].allSatisfy {
        guard let flag = features[$0] as? NSNumber else { return false }
        return CFGetTypeID(flag) == CFBooleanGetTypeID() && flag.boolValue
    }
}

// Do not forward the API credential to a redirect target.
final class HealthSessionDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

func healthSessionConfiguration() -> URLSessionConfiguration {
    let configuration = URLSessionConfiguration.ephemeral
    // A non-nil empty dictionary overrides system proxy settings for loopback.
    configuration.connectionProxyDictionary = [:]
    return configuration
}

final class SSHDiagnostics {
    private let lock = NSLock()
    private var bytes = Data()
    func append(_ data: Data) {
        lock.lock(); defer { lock.unlock() }
        bytes.append(data)
        if bytes.count > 2048 { bytes = Data(bytes.suffix(2048)) }
    }
    var detail: String {
        lock.lock(); defer { lock.unlock() }
        return String(decoding: bytes, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let statusLine = NSMenuItem(title: "Disconnected", action: nil, keyEquivalent: "")
    private var connectItem: NSMenuItem!
    private var disconnectItem: NSMenuItem!
    private var tunnel: Process?
    private var generation = UUID()
    private var timer: Timer?
    private var healthTask: URLSessionDataTask?
    private let healthSession = URLSession(configuration: healthSessionConfiguration(), delegate: HealthSessionDelegate(), delegateQueue: nil)
    private var config = BridgeConfig()
    private var settingsWindow: NSWindow?
    private let hostField = NSTextField()
    private let localField = NSTextField()
    private let remoteField = NSTextField()
    private let keyField = NSSecureTextField()
    private let settingsMessage = NSTextField(wrappingLabelWithString: "")

    private func brandImage() -> NSImage? {
        guard let url = Bundle.main.url(forResource: "menu-bar-template", withExtension: "png"),
              let image = NSImage(contentsOf: url) else { return nil }
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
        // A manually executed second binary must not create a competing tunnel.
        if let bundleID = Bundle.main.bundleIdentifier,
           NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
            .contains(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }) {
            NSApp.terminate(nil)
            return
        }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = brandImage()
        statusItem.button?.imagePosition = .imageLeading
        statusItem.button?.setAccessibilityLabel("Hermes Bridge Tool")
        setStatus("Disconnected", ready: false)
        let menu = NSMenu()
        menu.addItem(statusLine)
        menu.addItem(.separator())
        connectItem = menu.addItem(withTitle: "Connect", action: #selector(connect), keyEquivalent: "")
        disconnectItem = menu.addItem(withTitle: "Disconnect", action: #selector(disconnect), keyEquivalent: "")
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
                setStatus("Set up your connection", ready: false)
            }
        }
        catch { setStatus(error.localizedDescription, ready: false) }
    }

    private func setStatus(_ text: String, ready: Bool) {
        statusLine.title = text
        statusLine.toolTip = text
        let state = ready ? "●" : (tunnel == nil ? "○" : "◌")
        statusItem.button?.title = statusItem.button?.image == nil ? "Hermes Bridge Tool \(state)" : " \(state)"
        statusItem.button?.toolTip = text
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

    @objc private func connect() {
        guard tunnel == nil else { return }
        do {
            config = try BridgeConfig.load()
            _ = try config.readKey()
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
            process.arguments = config.sshArguments
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = FileHandle.nullDevice
            // Keep stderr available for an actionable error without blocking the UI.
            let errorPipe = Pipe()
            let diagnostics = SSHDiagnostics()
            errorPipe.fileHandleForReading.readabilityHandler = { handle in
                let bytes = handle.availableData
                if bytes.isEmpty { handle.readabilityHandler = nil }
                else { diagnostics.append(bytes) }
            }
            process.standardError = errorPipe
            let token = UUID()
            generation = token
            process.terminationHandler = { [weak self] finished in
                DispatchQueue.main.async {
                    guard let self, self.generation == token else { return }
                    let detail = diagnostics.detail
                    self.stopOwnedTunnel()
                    self.setStatus(detail.isEmpty ? "SSH stopped (exit \(finished.terminationStatus))." : detail, ready: false)
                }
            }
            try process.run()
            tunnel = process
            connectItem.isEnabled = false
            disconnectItem.isEnabled = true
            setStatus("Connecting to \(config.host)…", ready: false)
            timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in self?.checkHealth() }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in
                guard let self, self.generation == token else { return }
                self.checkHealth()
            }
        } catch { setStatus(error.localizedDescription, ready: false) }
    }

    private func checkHealth() {
        guard tunnel?.isRunning == true, healthTask == nil else { return }
        let token = generation
        do {
            let key = try config.readKey()
            var request = URLRequest(url: URL(string: "http://127.0.0.1:\(config.localPort)/v1/capabilities")!)
            request.timeoutInterval = 5
            request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
            healthTask = healthSession.dataTask(with: request) { [weak self] data, response, error in
                DispatchQueue.main.async {
                    guard let self, self.generation == token else { return }
                    self.healthTask = nil
                    if let error {
                        self.setStatus("API unavailable: \(error.localizedDescription)", ready: false)
                    } else if let response = response as? HTTPURLResponse, response.statusCode != 200 {
                        self.setStatus("API returned HTTP \(response.statusCode). Check API settings and key.", ready: false)
                    } else if let data, capabilitiesReady(data) {
                        self.setStatus("Ready · \(self.config.host) · localhost:\(self.config.localPort)", ready: true)
                    } else {
                        self.setStatus("Hermes API is missing required run capabilities.", ready: false)
                    }
                }
            }
            healthTask?.resume()
        } catch { setStatus(error.localizedDescription, ready: false) }
    }

    private func stopOwnedTunnel() {
        generation = UUID()
        timer?.invalidate(); timer = nil
        healthTask?.cancel(); healthTask = nil
        let owned = tunnel
        tunnel = nil
        if owned?.isRunning == true { owned?.terminate() }
        connectItem?.isEnabled = true
        disconnectItem?.isEnabled = false
    }

    @objc private func disconnect() {
        stopOwnedTunnel()
        setStatus("Disconnected", ready: false)
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
        let heading = NSTextField(labelWithString: "Connect to your Hermes agent")
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
        let note = NSTextField(wrappingLabelWithString: "Uses your existing SSH keys and host configuration. Settings take effect on the next connection.")
        note.textColor = .secondaryLabelColor
        note.frame = NSRect(x: 24, y: 95, width: 400, height: 42)
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
            if tunnel == nil { config = updated }
            keyField.stringValue = ""
            settingsWindow?.orderOut(nil)
        } catch { settingsMessage.stringValue = error.localizedDescription }
    }

    @objc private func quit() { NSApp.terminate(nil) }
    func applicationWillTerminate(_ notification: Notification) { stopOwnedTunnel() }
}

func selfTest() throws {
    let config = BridgeConfig()
    try config.validate()
    precondition(config.sshArguments.suffix(2) == ["127.0.0.1:18642:127.0.0.1:8642", "hetzner"])
    precondition(config.sshArguments.contains("ControlPath=none"))
    precondition(config.sshArguments.contains("StrictHostKeyChecking=yes"))
    for values: [String: Any] in [["ssh_host": "-oProxyCommand=bad"], ["ssh_host": "host;bad"],
                                 ["ssh_host": "host\n"], ["local_port": 0], ["remote_port": 65536], ["local_port": true],
                                 ["local_port": "123"], ["remote_port": 1.5], ["remote_port": 8642.0],
                                 ["ssh_host": String(repeating: "a", count: 256)]] {
        do { try BridgeConfig(values: values).validate(); fatalError("Invalid config accepted") }
        catch is BridgeError {}
    }
    precondition(capabilitiesReady(Data(#"{"features":{"run_submission":true,"run_status":true,"run_stop":true}}"#.utf8)))
    precondition(!capabilitiesReady(Data(#"{"features":{"run_submission":true,"run_status":true}}"#.utf8)))
    precondition(!capabilitiesReady(Data(#"{"features":{"run_submission":1,"run_status":true,"run_stop":true}}"#.utf8)))
    for key in ["", "has space", "has\ttab", "unicode-é", "line\nbreak"] {
        do { _ = try BridgeConfig.validatedKey(key); fatalError("Invalid API key accepted") }
        catch is BridgeError {}
    }
    let trimmedKey = try BridgeConfig.validatedKey("  test-key\n")
    precondition(trimmedKey == "test-key")
    precondition(healthSessionConfiguration().connectionProxyDictionary?.isEmpty == true)
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
    print("Hermes Bridge Tool: config, private storage, capabilities, and SSH arguments passed.")
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
