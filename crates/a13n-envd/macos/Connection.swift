import Foundation

func localized(_ message: String) -> String { NSLocalizedString(message, comment: "") }

struct ServiceTarget: Codable, Equatable {
    let service: String
    let workspace: String
    let host: String
}

struct SavedConnection: Codable {
    let target: ServiceTarget
    let directory: String
    let desktop: Bool
}

struct LauncherEvent: Decodable {
    struct Fields: Decodable {
        let code: String?
    }
    let event: String
    let data: Fields
}

/// Pipe framing is bounded and independent of OS chunk boundaries.
struct EventLines {
    private var pending = Data()

    mutating func append(_ bytes: Data) throws -> [LauncherEvent] {
        pending.append(bytes)
        var events: [LauncherEvent] = []
        while let newline = pending.firstIndex(of: 10) {
            guard newline < 65536 else { throw LauncherError.invalidOutput }
            let line = pending.prefix(upTo: newline)
            pending.removeSubrange(...newline)
            events.append(try JSONDecoder().decode(LauncherEvent.self, from: line))
        }
        guard pending.count < 65536 else { throw LauncherError.invalidOutput }
        return events
    }
}

enum LauncherError: LocalizedError {
    case alreadyRunning, invalidOutput, validation(String)
    var errorDescription: String? {
        switch self {
        case .alreadyRunning: return localized("Envd is already running. Disconnect before starting another connection.")
        case .invalidOutput: return localized("Envd returned an unsupported status. Install a matching version.")
        case .validation(let detail): return detail
        }
    }
}

/// All mutable state and callbacks belong to the main queue. No shell is involved.
final class ConnectionProcess {
    let executable: URL
    private(set) var process: Process?
    private var input: Pipe?
    private var stdout: Pipe?
    private var stderr: Pipe?
    private var lines = EventLines()
    private var errors = Data()
    var onEvent: ((LauncherEvent) -> Void)?
    var onExit: ((Int32, String) -> Void)?
    var isRunning: Bool { process != nil }

    init(executable: URL) { self.executable = executable }

    /// This short-lived helper only reads credentials and reports identity. It never owns a daemon.
    func recognize(link: String, completion: @escaping (Result<Bool, Error>) -> Void) {
        let executable = self.executable
        DispatchQueue.global(qos: .userInitiated).async {
            let result = Result<Bool, Error> {
                let child = Process(), output = Pipe(), error = Pipe()
                child.executableURL = executable
                child.arguments = ["connect-link", link, "--recognize"]
                child.environment = ProcessInfo.processInfo.environment.filter { !$0.key.hasPrefix("A13N_ENVD_") }
                child.standardInput = FileHandle.nullDevice
                child.standardOutput = output
                child.standardError = error
                try child.run()
                let data = output.fileHandleForReading.readDataToEndOfFile()
                let detail = error.fileHandleForReading.readDataToEndOfFile()
                child.waitUntilExit()
                guard child.terminationStatus == 0 else {
                    throw LauncherError.validation(String(decoding: detail.suffix(2000), as: UTF8.self))
                }
                struct Recognition: Decodable { let registered: Bool }
                return try JSONDecoder().decode(Recognition.self, from: data).registered
            }
            DispatchQueue.main.async { completion(result) }
        }
    }

    func inspect(link: String) throws -> ServiceTarget {
        let check = Process()
        check.executableURL = executable
        check.arguments = ["connect-link", link, "--check-json"]
        let output = Pipe(), error = Pipe()
        check.standardOutput = output
        check.standardError = error
        check.standardInput = FileHandle.nullDevice
        try check.run()
        // The bundled validator performs no network I/O and produces bounded output.
        let data = output.fileHandleForReading.readDataToEndOfFile()
        let detail = error.fileHandleForReading.readDataToEndOfFile()
        check.waitUntilExit()
        guard check.terminationStatus == 0 else {
            throw LauncherError.validation(String(decoding: detail, as: UTF8.self))
        }
        return try JSONDecoder().decode(ServiceTarget.self, from: data)
    }

    func start(arguments: [String]) throws {
        guard process == nil else { throw LauncherError.alreadyRunning }
        let child = Process(), input = Pipe(), output = Pipe(), error = Pipe()
        child.executableURL = executable
        child.arguments = arguments + ["--launcher"]
        // Inherited daemon configuration must not silently grant extra local access.
        var environment = ProcessInfo.processInfo.environment.filter { !$0.key.hasPrefix("A13N_ENVD_") }
        environment["A13N_ENVD_FULL_CONTROL"] = "1"
        environment["A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS"] = "300000"
        child.environment = environment
        child.standardInput = input
        child.standardOutput = output
        child.standardError = error
        lines = EventLines()
        errors = Data()
        self.input = input
        stdout = output
        stderr = error
        process = child
        output.fileHandleForReading.readabilityHandler = { [weak self, weak child] handle in
            let bytes = handle.availableData
            if bytes.isEmpty { handle.readabilityHandler = nil; return }
            DispatchQueue.main.async {
                guard let self, let child, self.process === child else { return }
                do {
                    for event in try self.lines.append(bytes) { self.onEvent?(event) }
                } catch {
                    self.errors.append(Data((error.localizedDescription + "\n").utf8))
                    self.stop()
                }
            }
        }
        error.fileHandleForReading.readabilityHandler = { [weak self, weak child] handle in
            let bytes = handle.availableData
            if bytes.isEmpty { handle.readabilityHandler = nil; return }
            DispatchQueue.main.async {
                guard let self, let child, self.process === child else { return }
                self.errors.append(bytes)
                self.errors = Data(self.errors.suffix(8192))
            }
        }
        child.terminationHandler = { [weak self] child in
            DispatchQueue.main.async {
                guard let self, self.process === child else { return }
                self.releasePipes()
                self.process = nil
                self.onExit?(child.terminationStatus, String(decoding: self.errors, as: UTF8.self))
            }
        }
        do { try child.run() }
        catch {
            process = nil
            releasePipes()
            throw error
        }
    }

    func stop() {
        // EOF also stops the daemon if the app is killed before it can send a signal.
        try? input?.fileHandleForWriting.close()
        if let child = process, child.isRunning { child.interrupt() }
    }

    private func releasePipes() {
        stdout?.fileHandleForReading.readabilityHandler = nil
        stderr?.fileHandleForReading.readabilityHandler = nil
        try? input?.fileHandleForWriting.close()
        input = nil
        stdout = nil
        stderr = nil
    }
}
