import AppKit

@main
final class EnvdApp: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var window: NSWindow!
    private var item: NSStatusItem!
    private let status = NSTextField(labelWithString: localized("Not connected"))
    private let destination = NSTextField(wrappingLabelWithString: localized("Choose Connect my computer in Console, then open A13N Envd."))
    private let code = NSTextField(labelWithString: "")
    private let explanation = NSTextField(wrappingLabelWithString: "")
    private let directory = NSTextField(string: FileManager.default.homeDirectoryForCurrentUser.path)
    private let desktop = NSButton(checkboxWithTitle: localized("Allow screenshots and desktop control"), target: nil, action: nil)
    private let start = NSButton(title: localized("Allow and connect"), target: nil, action: nil)
    private let stop = NSButton(title: localized("Disconnect"), target: nil, action: nil)
    private let copy = NSButton(title: localized("Copy code"), target: nil, action: nil)
    private let folder = NSButton(title: localized("Choose folder…"), target: nil, action: nil)
    private var verification: NSStackView!
    private var target: ServiceTarget?
    private var pendingLink: String?
    private var acceptedLink: String?
    private var recognizing = false
    private var queuedURL: URL?
    private var approved = false
    private var stopping = false
    private var quitting = false
    private lazy var connection = ConnectionProcess(
        executable: Bundle.main.resourceURL!.appendingPathComponent("a13n-envd")
    )
    private let settingsKey = "lastApprovedConnection"

    static func main() {
        let app = NSApplication.shared
        let delegate = EnvdApp()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.run()
        withExtendedLifetime(delegate) {}
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildWindow()
        buildMenu()
        connection.onEvent = { [weak self] event in self?.receive(event) }
        connection.onExit = { [weak self] status, detail in self?.exited(status, detail: detail) }
        if target == nil, let data = UserDefaults.standard.data(forKey: settingsKey),
           let saved = try? JSONDecoder().decode(SavedConnection.self, from: data) {
            target = saved.target
            directory.stringValue = saved.directory
            desktop.state = saved.desktop ? .on : .off
            approved = true
            showTarget()
            status.stringValue = localized("Saved connection · Not connected")
        }
        updateControls()
        showWindow()
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        guard let url = urls.first else { return }
        showWindow()
        let link = url.absoluteString
        guard !recognizing else { queuedURL = url; return }
        do {
            let inspected = try connection.inspect(link: link)
            recognizing = true
            updateControls()
            connection.recognize(link: link) { [weak self] result in
                guard let self else { return }
                self.recognizing = false
                defer {
                    self.updateControls()
                    if let queued = self.queuedURL {
                        self.queuedURL = nil
                        self.application(NSApplication.shared, open: [queued])
                    }
                }
                switch result {
                case .failure(let error):
                    self.notice(localized("Cannot check saved registration"), error.localizedDescription)
                case .success(let registered):
                    if self.connection.isRunning {
                        if registered {
                            self.explanation.stringValue = localized("Console now shows the existing computer and its current connection status. No pairing is needed.")
                        } else {
                            self.notice(localized("Envd is already running"), localized("Disconnect before connecting to a different workspace."))
                        }
                        return
                    }
                    self.target = inspected
                    self.pendingLink = registered ? nil : link
                    self.approved = registered
                    self.code.stringValue = ""
                    if registered, let data = UserDefaults.standard.data(forKey: self.settingsKey),
                       let saved = try? JSONDecoder().decode(SavedConnection.self, from: data), saved.target == inspected {
                        self.directory.stringValue = saved.directory
                        self.desktop.state = saved.desktop ? .on : .off
                    }
                    self.showTarget()
                    self.status.stringValue = localized(registered ? "Saved connection · Not connected" : "Waiting for your confirmation")
                    self.explanation.stringValue = localized(registered
                        ? "Console now shows the existing computer. Reconnect here when you want to use it; pairing is not needed."
                        : "Check the service and workspace, choose local permissions, then connect.")
                }
            }
        } catch { notice(localized("Cannot open connection"), error.localizedDescription) }
    }

    private func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 590),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "A13N Envd"
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 18
        root.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(root)
        NSLayoutConstraint.activate([
            root.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 28),
            root.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -28),
            root.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 28),
            root.bottomAnchor.constraint(lessThanOrEqualTo: window.contentView!.bottomAnchor, constant: -24),
        ])
        let title = NSTextField(labelWithString: localized("Connect this computer"))
        title.font = .systemFont(ofSize: 25, weight: .semibold)
        root.addArrangedSubview(title)
        status.font = .systemFont(ofSize: 14, weight: .medium)
        root.addArrangedSubview(status)
        destination.isSelectable = true
        root.addArrangedSubview(destination)
        code.font = .monospacedDigitSystemFont(ofSize: 38, weight: .semibold)
        code.isSelectable = true
        verification = NSStackView(views: [code, copy])
        verification.spacing = 16
        root.addArrangedSubview(verification)
        root.addArrangedSubview(explanation)
        root.addArrangedSubview(NSTextField(labelWithString: localized("Starting directory")))
        let directories = NSStackView(views: [directory, folder])
        directory.setContentHuggingPriority(.defaultLow, for: .horizontal)
        root.addArrangedSubview(directories)
        directories.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        root.addArrangedSubview(desktop)
        let permissions = NSTextField(wrappingLabelWithString:
            localized("Users and service accounts with Run permission in this workspace can use agents to read and change files and run Shell commands. The starting directory does not limit file access. Desktop control also requires macOS Screen Recording and Accessibility permission."))
        permissions.font = .systemFont(ofSize: 12)
        permissions.textColor = .secondaryLabelColor
        root.addArrangedSubview(permissions)
        let buttons = NSStackView(views: [start, stop])
        buttons.spacing = 12
        root.addArrangedSubview(buttons)
        let hint = NSTextField(wrappingLabelWithString: localized("Closing this window keeps the connection. Reopen it from the menu bar. Quitting the app disconnects."))
        hint.font = .systemFont(ofSize: 12)
        hint.textColor = .secondaryLabelColor
        root.addArrangedSubview(hint)
        for text in [destination, explanation, permissions, hint] {
            text.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        }
        start.target = self; start.action = #selector(connect)
        window.defaultButtonCell = start.cell as? NSButtonCell
        stop.target = self; stop.action = #selector(disconnect)
        copy.target = self; copy.action = #selector(copyCode)
        folder.target = self; folder.action = #selector(chooseFolder)
    }

    private func buildMenu() {
        let menu = NSMenu()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: localized("Show A13N Envd"), action: #selector(showWindow), keyEquivalent: "0").target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: localized("Quit A13N Envd"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let main = NSMenuItem()
        main.submenu = appMenu
        menu.addItem(main)
        NSApplication.shared.mainMenu = menu
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = NSImage(systemSymbolName: "desktopcomputer", accessibilityDescription: "A13N Envd")
        item.menu = appMenu.copy() as? NSMenu
    }

    @objc private func showWindow() {
        window?.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    private func showTarget() {
        guard let target else { return }
        destination.stringValue = String(format: localized("Service: %@\nWorkspace: %@"), target.service, target.workspace)
    }

    @objc private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.directoryURL = URL(fileURLWithPath: directory.stringValue)
        if panel.runModal() == .OK, let url = panel.url { directory.stringValue = url.path }
    }

    @objc private func connect() {
        guard let target, !connection.isRunning, !recognizing else { return }
        var isDirectory: ObjCBool = false
        guard directory.stringValue.hasPrefix("/"),
              FileManager.default.fileExists(atPath: directory.stringValue, isDirectory: &isDirectory),
              isDirectory.boolValue else {
            notice(localized("Directory unavailable"), localized("Choose an existing directory on this computer."))
            return
        }
        let arguments = pendingLink.map { ["connect-link", $0] } ?? ["connect-service", target.host]
        do {
            try connection.start(arguments: arguments + [
                "--name", Host.current().localizedName ?? "My computer",
                "--default-working-directory", directory.stringValue,
                "--computer-use", desktop.state == .on ? "true" : "false",
            ])
            acceptedLink = pendingLink
            stopping = false
            status.stringValue = pendingLink == nil ? localized("Connecting…") : localized("Requesting pairing…")
            explanation.stringValue = desktop.state == .on
                ? localized("If macOS asks for permissions, complete those prompts first.") : localized("Keep this app running.")
            updateControls()
        } catch { notice(localized("Cannot start Envd"), error.localizedDescription) }
    }

    private func receive(_ event: LauncherEvent) {
        if stopping { return }
        switch event.event {
        case "verification":
            guard let value = event.data.code, value.count == 6,
                  value.allSatisfy({ $0.isASCII && $0.isNumber }) else { return }
            code.stringValue = value
            status.stringValue = localized("Waiting for Console approval")
            explanation.stringValue = localized("Enter this code in Console, check the computer details, then approve.")
        case "approved":
            approved = true
            pendingLink = nil
            code.stringValue = ""
            saveConnection()
            status.stringValue = localized("Approved · Connecting…")
        case "connecting": status.stringValue = localized("Connecting…")
        case "online":
            status.stringValue = localized("Online")
            explanation.stringValue = localized("Connected. Select this computer in your Agent or conversation settings in Console.")
            if approved { saveConnection() }
        case "reconnecting":
            status.stringValue = localized("Disconnected · Reconnecting…")
            explanation.stringValue = localized("Envd reconnects automatically. You do not need to open the connection link again.")
        default: break
        }
        item?.button?.toolTip = "A13N Envd · " + status.stringValue
        status.textColor = event.event == "online" ? .systemGreen : .labelColor
        updateControls()
    }

    private func saveConnection() {
        guard let target,
              let data = try? JSONEncoder().encode(SavedConnection(target: target, directory: directory.stringValue,
                                                                 desktop: desktop.state == .on)) else { return }
        UserDefaults.standard.set(data, forKey: settingsKey)
    }

    private func exited(_ exit: Int32, detail: String) {
        code.stringValue = ""
        if stopping || exit == 0 {
            status.stringValue = localized("Disconnected")
            explanation.stringValue = ""
        } else {
            status.stringValue = localized("Connection failed")
            explanation.stringValue = detail.contains("runtime parent is already owned")
                ? localized("Another Envd process is using this computer. If the old Terminal process is still running, press Ctrl+C there, then connect here.")
                : String(detail.suffix(1000))
        }
        stopping = false
        updateControls()
        if quitting { NSApplication.shared.reply(toApplicationShouldTerminate: true) }
    }

    @objc private func disconnect() {
        guard connection.isRunning else { return }
        stopping = true
        status.stringValue = localized("Disconnecting…")
        connection.stop()
        updateControls()
    }

    @objc private func copyCode() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(code.stringValue, forType: .string)
    }

    private func updateControls() {
        start.isEnabled = !recognizing && target != nil && !connection.isRunning
        start.title = approved ? localized("Reconnect") : localized("Allow and connect")
        stop.isEnabled = connection.isRunning && !stopping
        directory.isEnabled = !connection.isRunning
        folder.isEnabled = !connection.isRunning
        desktop.isEnabled = !connection.isRunning
        copy.isHidden = code.stringValue.isEmpty
        verification?.isHidden = code.stringValue.isEmpty
        window?.setContentSize(NSSize(width: 560, height: code.stringValue.isEmpty ? 520 : 590))
    }

    private func notice(_ title: String, _ detail: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.runModal()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        showWindow(); return true
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard connection.isRunning else { return .terminateNow }
        quitting = true
        disconnect()
        return .terminateLater
    }
}
