import Foundation

@main
struct ConnectionTests {
    static func wait(_ predicate: () -> Bool) {
        let deadline = Date().addingTimeInterval(5)
        while !predicate() && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.01))
        }
        precondition(predicate(), "Timed out waiting for child lifecycle")
    }

    static func main() throws {
        var lines = EventLines()
        let incomplete = try lines.append(Data("{\"event\":\"verifi".utf8))
        precondition(incomplete.isEmpty)
        let events = try lines.append(Data("cation\",\"data\":{\"code\":\"123456\"}}\n{\"event\":\"online\",\"data\":{}}\n".utf8))
        precondition(events.map { $0.event } == ["verification", "online"])
        precondition(events[0].data.code == "123456")
        do {
            _ = try lines.append(Data(repeating: 65, count: 65536))
            fatalError("Accepted unbounded output")
        } catch {}

        let process = ConnectionProcess(executable: URL(fileURLWithPath: CommandLine.arguments[1]))
        let target = try process.inspect(link: "literal link")
        precondition(target.workspace == "ws_test")
        var seen: [String] = []
        var exits = 0
        process.onEvent = { seen.append($0.event) }
        process.onExit = { status, detail in precondition(status == 0, "Exit \(status): \(detail)"); exits += 1 }
        let args = ["connect-service", "saved", "--default-working-directory", "/tmp/a b;$(touch forbidden)"]
        try process.start(arguments: args)
        do {
            try process.start(arguments: args)
            fatalError("Spawned a duplicate daemon")
        } catch LauncherError.alreadyRunning {}
        wait { seen == ["verification", "online"] }
        let pid = process.process!.processIdentifier
        var recognized: Bool? = nil
        process.recognize(link: "literal link") { recognized = try? $0.get() }
        wait { recognized == true }
        precondition(process.process!.processIdentifier == pid && process.isRunning)
        var refused = false
        process.recognize(link: "invalid link") { result in
            if case .failure = result { refused = true }
        }
        wait { refused }
        precondition(process.process!.processIdentifier == pid && process.isRunning)
        process.stop()
        wait { exits == 1 }
        precondition(!process.isRunning)
        seen = []
        try process.start(arguments: args)
        wait { seen == ["verification", "online"] }
        process.stop()
        wait { exits == 2 }
        print("PASS: bounded fragmented events, literal arguments, single child, disconnect and reconnect")
    }
}
