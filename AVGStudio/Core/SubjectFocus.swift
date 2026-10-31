import Foundation

// Where the subject is in each picture - so a close-up lands on it.
//
// The shot-cut planner (EditPlan.swift) crops into pictures. Without knowing
// what is in them it aims at the upper middle, which is right for a typical
// portrait or a centred object and wrong for everything else. One small vision
// call per video (the pictures, reduced to thumbnails, in a single request)
// returns a centre point per picture; the planner crops around it.
//
// This file is the part that does not touch the network: what a point is, how
// a model's answer is read (leniently - it is free text from a model), and how
// we tell the saved answer still matches the pictures.

struct FocusPoint: Codable, Hashable {
    /// 0-based scene index.
    var scene: Int
    /// 0...1 from the top-left of the picture.
    var x: Double
    var y: Double
}

/// The answer for one video, tied to the exact pictures it was asked about:
/// regenerate a picture and the signature changes, so the old answer is not used.
struct SubjectFocus: Codable, Hashable {
    var signature: String
    var points: [FocusPoint]
}

extension SubjectFocus {
    /// A stable fingerprint of the pictures (FNV-1a, not `Hasher`, which is
    /// seeded differently on every launch).
    static func signature(_ parts: [String]) -> String {
        var hash: UInt64 = 0xcbf29ce484222325
        for byte in parts.joined(separator: "|").utf8 {
            hash ^= UInt64(byte)
            hash = hash &* 0x100000001b3
        }
        return String(hash, radix: 16)
    }

    /// Reads the model's reply: `{"pictures":[{"picture":1,"x":0.4,"y":0.3}, ...]}`
    /// (a bare array is accepted too). Pictures are numbered from 1; `valid`
    /// holds the 0-based scenes that were actually sent. Percentages (40 meaning
    /// 0.40) are accepted, everything is clamped to 0...1, and anything unusable
    /// is skipped rather than failing the whole answer.
    static func parse(_ raw: String, valid: Set<Int>) -> [FocusPoint] {
        guard let items = pictures(in: raw) else { return [] }
        var seen = Set<Int>()
        var out: [FocusPoint] = []
        for item in items {
            guard let number = double(item["picture"] ?? item["scene"]),
                  let x = double(item["x"]), let y = double(item["y"]) else { continue }
            let scene = Int(number.rounded()) - 1
            guard valid.contains(scene), !seen.contains(scene) else { continue }
            seen.insert(scene)
            out.append(FocusPoint(scene: scene, x: unit(x), y: unit(y)))
        }
        return out.sorted { $0.scene < $1.scene }
    }

    private static func pictures(in raw: String) -> [[String: Any]]? {
        if let json = Pipeline.extractJSONObject(raw), let data = json.data(using: .utf8),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let list = object["pictures"] as? [[String: Any]] {
            return list
        }
        if let data = raw.trimmed.data(using: .utf8),
           let list = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] {
            return list
        }
        return nil
    }

    private static func double(_ value: Any?) -> Double? {
        if let n = value as? NSNumber { return n.doubleValue }
        if let s = value as? String { return Double(s.trimmed) }
        return nil
    }

    /// 0.4 stays 0.4; 40 becomes 0.4; anything else (1.2, -3) is clamped.
    private static func unit(_ v: Double) -> Double {
        let scaled = (v > 1.5 && v <= 100) ? v / 100 : v
        return min(max(scaled, 0), 1)
    }
}
