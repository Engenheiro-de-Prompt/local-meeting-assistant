import AppKit
import Foundation

let productionOrigin = "http://127.0.0.1:18766"

struct MonitorError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

func baseURL(_ raw: String) throws -> URL {
    guard let u = URLComponents(string: raw), u.scheme == "http", u.host == "127.0.0.1",
          let port = u.port, (1...65535).contains(port), u.user == nil, u.password == nil,
          u.query == nil, u.fragment == nil, u.path.isEmpty || u.path == "/",
          let url = u.url else {
        throw MonitorError(message: "Use only http://127.0.0.1:PORT without credentials, path or query")
    }
    return url
}

// Never follow backend redirects (including redirects to other loopback services).
final class LocalClient: NSObject, URLSessionDataDelegate {
    let base: URL
    private var bytes = Data()
    private var response: HTTPURLResponse?
    private var completion: ((Result<[String: Any], Error>) -> Void)?
    private var sizeExceeded = false
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 4
        config.timeoutIntervalForResource = 6
        config.httpMaximumConnectionsPerHost = 1
        config.httpShouldSetCookies = false
        config.urlCache = nil
        config.urlCredentialStorage = nil
        config.connectionProxyDictionary = [:]
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()
    init(base: URL) { self.base = base }
    // Caller serializes operations, so polling and manual mutation cannot overlap.
    func request(recordingOnly: Bool = false, completion: @escaping (Result<[String: Any], Error>) -> Void) {
        self.completion = completion
        bytes = Data(); response = nil; sizeExceeded = false
        var request = URLRequest(url: base.appendingPathComponent(recordingOnly ? "api/resource-guard" : "api/status"))
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if recordingOnly {
            request.httpMethod = "POST"
            request.setValue(productionOrigin, forHTTPHeaderField: "Origin")
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = Data("{\"recording_only\":true}".utf8)
        }
        session.dataTask(with: request).resume()
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        self.response = response as? HTTPURLResponse
        if response.expectedContentLength > 8 * 1024 * 1024 {
            sizeExceeded = true; completionHandler(.cancel)
        } else { completionHandler(.allow) }
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        if bytes.count + data.count > 8 * 1024 * 1024 {
            sizeExceeded = true; dataTask.cancel()
        } else { bytes.append(data) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let result: Result<[String: Any], Error>
        do {
            if sizeExceeded { throw MonitorError(message: "Status exceeds 8 MiB safety limit") }
            if let error = error { throw error }
            guard let code = response?.statusCode, (200...299).contains(code) else {
                throw MonitorError(message: "HTTP \(response?.statusCode ?? 0); backend unavailable or command rejected")
            }
            guard let json = try JSONSerialization.jsonObject(with: bytes) as? [String: Any] else {
                throw MonitorError(message: "Invalid status: expected JSON object")
            }
            result = .success(json)
        } catch { result = .failure(error) }
        let callback = completion
        completion = nil
        callback?(result)
    }
}

func scalar(_ value: Any) -> String {
    if value is NSNull { return "unavailable" }
    return String(describing: value).replacingOccurrences(of: "\n", with: " ")
}

// Render only operational metadata; never display/log transcript, notes or analysis.
func flatten(_ dict: [String: Any], prefix: String = "", depth: Int = 0) -> [String] {
    guard depth < 4 else { return [] }
    return dict.keys.sorted().prefix(64).flatMap { key -> [String] in
        let path = prefix.isEmpty ? key : "\(prefix).\(key)"
        if let nested = dict[key] as? [String: Any] { return flatten(nested, prefix: path, depth: depth + 1) }
        return ["\(path): \(String(scalar(dict[key]! ).prefix(300)))"]
    }
}

struct Snapshot {
    let state: String
    let guardAvailable: Bool
    let lines: [String]
    init(_ json: [String: Any]) throws {
        guard let state = json["state"] as? String, !state.isEmpty else {
            throw MonitorError(message: "Invalid status: missing capture/session state")
        }
        self.state = state
        let guardState = json["resource_guard"] as? [String: Any] ?? [:]
        guardAvailable = !guardState.isEmpty
        var lines = ["Session state: \(state)"]
        if let capture = json["capture_state"] { lines.append("Capture state: \(scalar(capture))") }
        if let elapsed = json["elapsed_s"] { lines.append("Elapsed: \(scalar(elapsed)) s") }
        let progress = json["processing_progress"] as? [String: Any] ?? [:]
        lines += flatten(progress, prefix: "capture/progress")
        if let captureAlive = guardState["capture_alive"] {
            lines.append("Capture alive (backend): \(scalar(captureAlive))")
        } else if progress.isEmpty && json["capture_state"] == nil {
            lines.append("Capture health: unavailable (session state is not proof of capture)")
        }
        lines.append("Resource guard:")
        lines += guardAvailable ? flatten(guardState) : ["unavailable — backend does not report resource_guard"]
        for key in ["analysis_interval_s", "transcription_interval_s", "whisper_interval_s", "effective_analysis_interval_s", "effective_transcription_interval_s", "effective_whisper_interval_s"] {
            if let value = json[key] { lines.append("\(key): \(scalar(value)) s") }
        }
        let usage = json["resource_usage"] as? [String: Any] ?? [:]
        lines.append("Resource usage (backend units; peak/min are historical):")
        lines += usage.isEmpty ? ["unavailable — no resource samples"] : flatten(usage)
        for key in ["error", "analysis_error"] {
            if let error = json[key] as? String, !error.isEmpty { lines.append("\(key): \(String(error.prefix(300)))") }
        }
        self.lines = lines
    }
}

final class MonitorDelegate: NSObject, NSApplicationDelegate {
    let client: LocalClient
    var statusItem: NSStatusItem!
    var timer: Timer?
    var busy = false
    var failures = 0
    var lastSuccess: Date?
    var snapshot: Snapshot?
    var notice = "Waiting for backend — not yet sampled"
    init(base: URL) { client = LocalClient(base: base) }
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "LMA ?"
        render()
        poll()
    }
    func render() {
        let menu = NSMenu()
        menu.autoenablesItems = false
        let age = lastSuccess.map { "Last status received: \(DateFormatter.localizedString(from: $0, dateStyle: .none, timeStyle: .medium))" } ?? "No successful sample"
        let lines = [notice, age] + (snapshot?.lines ?? [])
        for line in lines {
            // Submenus preserve full bounded field text without making a giant menu.
            let item = NSMenuItem(title: String(line.prefix(115)), action: nil, keyEquivalent: "")
            item.isEnabled = false
            item.toolTip = line
            menu.addItem(item)
        }
        menu.addItem(.separator())
        let recording = NSMenuItem(title: "Recording only (pause AI)", action: #selector(recordingOnly), keyEquivalent: "")
        recording.target = self
        recording.isEnabled = !busy && failures == 0 && snapshot?.guardAvailable == true
        menu.addItem(recording)
        let dashboard = NSMenuItem(title: "Open dashboard…", action: #selector(openDashboard), keyEquivalent: "")
        dashboard.target = self; dashboard.isEnabled = true; menu.addItem(dashboard)
        let quit = NSMenuItem(title: "Quit monitor (keeps recording)", action: #selector(quitMonitor), keyEquivalent: "q")
        quit.target = self; quit.isEnabled = true; menu.addItem(quit)
        statusItem.menu = menu
        statusItem.button?.title = failures > 0 ? "LMA !" : "LMA \(snapshot?.state == "recording" ? "●" : "○")"
        statusItem.button?.toolTip = "\(notice) — \(age)"
    }
    func schedule() {
        timer?.invalidate()
        let delay = failures == 0 ? 5.0 : min(60.0, 10.0 * pow(2.0, Double(min(failures - 1, 3))))
        timer = Timer(timeInterval: delay, repeats: false) { [weak self] _ in self?.poll() }
        RunLoop.main.add(timer!, forMode: .common)
    }
    func poll() {
        guard !busy else { return }
        busy = true
        client.request { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.busy = false
                do {
                    let json = try result.get()
                    self.snapshot = try Snapshot(json)
                    self.lastSuccess = Date(); self.failures = 0
                    self.notice = "Backend connected (5 s polling; not a safety guarantee)"
                } catch {
                    self.failures += 1
                    self.notice = "STALE / unavailable: \(error.localizedDescription)"
                }
                self.render(); self.schedule()
            }
        }
    }
    @objc func recordingOnly() {
        guard !busy, failures == 0, snapshot?.guardAvailable == true else { return }
        timer?.invalidate(); busy = true; notice = "Requesting recording only — awaiting backend"; render()
        client.request(recordingOnly: true) { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.busy = false
                switch result {
                case .success:
                    // Do not assume a POST success means policy applied: read back /api/status.
                    self.notice = "Command accepted; reading effective backend state"
                    self.poll()
                case .failure(let error):
                    self.failures += 1; self.notice = "Command failed: \(error.localizedDescription)"
                    self.render(); self.schedule()
                }
            }
        }
    }
    @objc func openDashboard() { NSWorkspace.shared.open(client.base) }
    @objc func quitMonitor() { NSApp.terminate(nil) }
}

func headless(client: LocalClient, recordingOnly: Bool) throws {
    func perform(_ mutate: Bool) throws -> [String: Any] {
        let signal = DispatchSemaphore(value: 0)
        var received: Result<[String: Any], Error>?
        client.request(recordingOnly: mutate) { result in received = result; signal.signal() }
        guard signal.wait(timeout: .now() + 9) == .success, let result = received else {
            throw MonitorError(message: "HTTP fixture timed out")
        }
        return try result.get()
    }
    if recordingOnly { _ = try perform(true) }
    let snapshot = try Snapshot(perform(false))
    print(snapshot.lines.joined(separator: "\n"))
}

do {
    let args = Array(CommandLine.arguments.dropFirst())
    var rawBase = productionOrigin
    var selfTest = false
    var recordingOnly = false
    var index = 0
    while index < args.count {
        switch args[index] {
        case "--self-test": selfTest = true
        case "--recording-only": recordingOnly = true
        case "--base-url":
            index += 1
            guard index < args.count else { throw MonitorError(message: "Missing --base-url value") }
            rawBase = args[index]
        default: throw MonitorError(message: "Unknown argument: \(args[index])")
        }
        index += 1
    }
    let base = try baseURL(rawBase)
    if recordingOnly && (!selfTest || base.port == 18766) {
        throw MonitorError(message: "--recording-only is only allowed with --self-test on a non-production fixture port")
    }
    if selfTest { try headless(client: LocalClient(base: base), recordingOnly: recordingOnly) }
    else {
        let app = NSApplication.shared
        let delegate = MonitorDelegate(base: base)
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
} catch {
    FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
    exit(1)
}
