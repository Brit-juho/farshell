import Foundation
import Speech
import AVFoundation

let locale = Locale(identifier: "ko-KR")

func out(_ o: [String: String]) {
    let d = try! JSONSerialization.data(withJSONObject: o, options: [.withoutEscapingSlashes])
    FileHandle.standardOutput.write(d + Data([10]))
}

func makeTranscriber() throws -> SpeechTranscriber {
    guard SpeechTranscriber.isAvailable else { throw NSError(domain: "stt", code: 1, userInfo: [NSLocalizedDescriptionKey: "SpeechTranscriber unavailable"]) }
    return SpeechTranscriber(locale: locale, preset: .transcription)
}

// 자산이 이미 설치됐는가(내려받지 않는다).
func assetsInstalled() async throws -> Bool {
    let t = try makeTranscriber()
    // status(forModules:)가 설치돼 있어도 .supported로 답하는 OS가 있어 설치된 로케일 목록을 같이 본다.
    if await AssetInventory.status(forModules: [t]) == .installed { return true }
    let want = locale.identifier(.bcp47)
    return await SpeechTranscriber.installedLocales.contains { $0.identifier(.bcp47) == want }
}

// 사용자가 요청했을 때만 불린다(--install). 진행은 {"progress":done,"total":total} 줄로 알린다.
func installAssets() async throws {
    let t = try makeTranscriber()
    guard let req = try await AssetInventory.assetInstallationRequest(supporting: [t]) else { return }
    let prog = req.progress
    let poller = Task {
        while !Task.isCancelled {
            out(["progress": String(prog.completedUnitCount), "total": String(prog.totalUnitCount)])
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
    }
    defer { poller.cancel() }
    try await req.downloadAndInstall()
}

func transcribe(_ path: String) async throws -> String {
    let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
    let t = SpeechTranscriber(locale: locale, preset: .transcription)
    let analyzer = SpeechAnalyzer(modules: [t])
    let collector = Task { () -> String in
        var s = ""
        for try await r in t.results where r.isFinal { s += String(r.text.characters) }
        return s
    }
    if let last = try await analyzer.analyzeSequence(from: file) {
        try await analyzer.finalizeAndFinish(through: last)
    } else {
        await analyzer.cancelAndFinishNow()
    }
    return try await collector.value.trimmingCharacters(in: .whitespacesAndNewlines)
}

func run(_ p: String, json: Bool) async {
    do {
        let s = try await transcribe(p)
        if json { out(["path": p, "text": s]) } else { print(s) }
    } catch {
        if json { out(["path": p, "error": "\(error)"]) }
        else { FileHandle.standardError.write("\(error)\n".data(using: .utf8)!); exit(1) }
    }
}

@main struct Main {
    static func main() async {
        setvbuf(stdout, nil, _IOLBF, 0)
        var args = Array(CommandLine.arguments.dropFirst())
        func fail(_ msg: String, _ code: Int32) -> Never {
            FileHandle.standardError.write("\(msg)\n".data(using: .utf8)!); exit(code)
        }
        if args.first == "--check" {
            do { out(["installed": try await assetsInstalled() ? "1" : "0"]) } catch { fail("check error: \(error)", 2) }
            return
        }
        if args.first == "--install" {
            do { try await installAssets(); out(["installed": "1"]) } catch { fail("install error: \(error)", 2) }
            return
        }
        // 이하 모드는 자산을 절대 몰래 내려받지 않는다 — 없으면 종료(3).
        do { if try await !assetsInstalled() { fail("asset missing: ko-KR", 3) } } catch { fail("asset error: \(error)", 2) }
        if args.first == "--serve" {
            out(["ready": "1"])
            while let line = readLine() {
                let p = line.trimmingCharacters(in: .whitespaces)
                if !p.isEmpty { await run(p, json: true) }
            }
        } else if args.count == 1 {
            await run(args.removeFirst(), json: false)
        } else if args.isEmpty {
            FileHandle.standardError.write("usage: stt_apple [--serve|--check|--install] | file.wav [file2.wav ...]\n".data(using: .utf8)!); exit(64)
        } else {
            for p in args { await run(p, json: true) }
        }
    }
}
