import Foundation

/// One app setting the assistant may read and (with approval) change.
struct AgentSetting {
    enum Group: String, CaseIterable {
        case ai, images, voice, captions, video, performance, look
        var title: String {
            switch self {
            case .ai: return "Writer"
            case .images: return "Images"
            case .voice: return "Voice"
            case .captions: return "Captions"
            case .video: return "Video"
            case .performance: return "Performance"
            case .look: return "App look"
            }
        }
    }

    struct Option: Equatable {
        let value: String
        let label: String
    }

    enum Kind {
        case choice([Option])
        case toggle
        case number(ClosedRange<Int>)
        /// A model id for whichever service is currently chosen.
        case model
        case text(max: Int)
    }

    /// What the model writes (`writer`, `quality`, ...): short and stable.
    let key: String
    let title: String
    let group: Group
    let kind: Kind
    /// The current value in its canonical form: an option's value, "on"/"off", digits, or the text.
    let read: (AppSettings) -> String
    /// Writes an already-validated canonical value. false = it cannot apply
    /// with the current choices (a model id while the writer is on-device).
    let write: (inout AppSettings, String) -> Bool
}

/// What a change did, in words for the approval card and the result.
struct AgentSettingChange: Equatable {
    let title: String
    let from: String
    let to: String
}

/// THE list of settings the assistant can touch, written by hand rather than
/// derived from `AppSettings`: a setting is here because changing it for the
/// person is useful and safe. Deliberately NOT here: API keys and backup
/// keys, everything `agent...` (its own capabilities, spending limit,
/// approval mode, failover, instructions - it must not loosen its own
/// guardrails), onboarding, and the per-service voice ids and fallback model
/// names. A test pins that nothing under `agent...` is reachable.
enum AgentSettingsCatalog {

    // MARK: Builders

    private static func choice<E: RawRepresentable & CaseIterable>(
        _ key: String, _ title: String, _ group: AgentSetting.Group,
        _ path: WritableKeyPath<AppSettings, E>, label: @escaping (E) -> String
    ) -> AgentSetting where E.RawValue == String {
        AgentSetting(key: key, title: title, group: group,
                     kind: .choice(E.allCases.map { AgentSetting.Option(value: $0.rawValue, label: label($0)) }),
                     read: { $0[keyPath: path].rawValue },
                     write: { s, v in
                         guard let e = E(rawValue: v) else { return false }
                         s[keyPath: path] = e
                         return true
                     })
    }

    private static func intChoice(_ key: String, _ title: String, _ group: AgentSetting.Group,
                                  _ path: WritableKeyPath<AppSettings, Int>, values: [Int],
                                  label: @escaping (Int) -> String) -> AgentSetting {
        AgentSetting(key: key, title: title, group: group,
                     kind: .choice(values.map { AgentSetting.Option(value: String($0), label: label($0)) }),
                     read: { String($0[keyPath: path]) },
                     write: { s, v in
                         guard let n = Int(v), values.contains(n) else { return false }
                         s[keyPath: path] = n
                         return true
                     })
    }

    private static func toggle(_ key: String, _ title: String, _ group: AgentSetting.Group,
                               _ path: WritableKeyPath<AppSettings, Bool>) -> AgentSetting {
        AgentSetting(key: key, title: title, group: group, kind: .toggle,
                     read: { $0[keyPath: path] ? "on" : "off" },
                     write: { s, v in
                         guard v == "on" || v == "off" else { return false }
                         s[keyPath: path] = (v == "on")
                         return true
                     })
    }

    private static func number(_ key: String, _ title: String, _ group: AgentSetting.Group,
                               _ path: WritableKeyPath<AppSettings, Int>, range: ClosedRange<Int>) -> AgentSetting {
        AgentSetting(key: key, title: title, group: group, kind: .number(range),
                     read: { String($0[keyPath: path]) },
                     write: { s, v in
                         guard let n = Int(v), range.contains(n) else { return false }
                         s[keyPath: path] = n
                         return true
                     })
    }

    private static func capitalised(_ s: String) -> String { s.prefix(1).uppercased() + s.dropFirst() }

    // MARK: The catalogue

    static let all: [AgentSetting] = [
        // Writer
        choice("writer", "Video writer", .ai, \.text, label: { $0.label }),
        AgentSetting(key: "writer_model", title: "Writer model", group: .ai, kind: .model,
                     read: { s in
                         switch s.text {
                         case .gemini: return s.geminiModel
                         case .groq: return s.groqModel
                         case .pollinations: return s.pollinationsTextModel
                         case .apple: return ""
                         }
                     },
                     write: { s, v in
                         switch s.text {
                         case .gemini: s.geminiModel = v
                         case .groq: s.groqModel = v
                         case .pollinations: s.pollinationsTextModel = v
                         case .apple: return false
                         }
                         return true
                     }),
        toggle("auto_fallback", "Switch to the fallback model by itself when quota runs out", .ai, \.autoUseFallback),

        // Images
        choice("images", "Image maker", .images, \.image, label: { $0.label }),
        AgentSetting(key: "image_model", title: "Image model", group: .images, kind: .model,
                     read: { s in
                         switch s.image {
                         case .gemini: return s.geminiImageModel
                         case .pollinations: return s.pollinationsImageModel
                         case .stablehorde: return s.stableHordeModel
                         case .openai: return ""
                         }
                     },
                     write: { s, v in
                         switch s.image {
                         case .gemini: s.geminiImageModel = v
                         case .pollinations: s.pollinationsImageModel = v
                         case .stablehorde: s.stableHordeModel = v
                         case .openai: return false
                         }
                         return true
                     }),
        AgentSetting(key: "image_style", title: "Image style", group: .images, kind: .text(max: 200),
                     read: { $0.imageStyle },
                     write: { s, v in s.imageStyle = v; return true }),

        // Voice
        choice("voice", "Voice service", .voice, \.voice, label: { $0.label }),

        // Captions
        toggle("captions", "Captions", .captions, \.captionsEnabled),
        number("caption_words", "Words per caption", .captions, \.captionMaxWords, range: 1...10),
        choice("caption_position", "Caption position", .captions, \.captionPosition, label: { capitalised($0.rawValue) }),
        choice("caption_style", "Caption animation", .captions, \.captionAnimationStyle, label: { $0.label }),

        // Video
        intChoice("quality", "Quality", .video, \.shortEdge, values: [720, 1080, 1440], label: { "\($0)p" }),
        intChoice("fps", "Frame rate", .video, \.fps, values: [24, 30, 60], label: { "\($0) fps" }),
        toggle("ken_burns", "Slow zoom on images", .video, \.kenBurns),
        choice("filter", "Film filter", .video, \.filterStyle, label: { $0.label }),
        toggle("pauses", "Dynamic pauses", .video, \.dynamicPausesEnabled),

        // Performance
        intChoice("videos_at_once", "Videos at once", .performance, \.maxParallelJobs, values: [1, 2, 3], label: { "\($0)" }),
        intChoice("images_at_once", "Images at once", .performance, \.imageParallelism, values: [1, 2, 3, 4], label: { "\($0)" }),
        toggle("keep_awake", "Keep the screen awake while making a video", .performance, \.keepScreenAwake),
        toggle("background_work", "Keep working when I leave the app", .performance, \.allowBackgroundRendering),
        toggle("cool_down", "Wait for the phone to cool down", .performance, \.pauseWhenHot),

        // App look
        choice("theme", "Theme", .look, \.themePreference, label: { $0.label }),
        choice("font", "App font", .look, \.appFont, label: { $0.label }),
        AgentSetting(key: "glass", title: "Glass opacity (15-100)", group: .look, kind: .number(15...100),
                     read: { String(Int(($0.glassOpacity * 100).rounded())) },
                     write: { s, v in
                         guard let n = Int(v), (15...100).contains(n) else { return false }
                         s.glassOpacity = Double(n) / 100
                         return true
                     }),
    ]

    static var keys: [String] { all.map { $0.key } }

    static func setting(_ key: String) -> AgentSetting? {
        let k = key.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return all.first { $0.key == k }
    }

    static func settings(in group: AgentSetting.Group) -> [AgentSetting] { all.filter { $0.group == group } }

    // MARK: Reading

    /// The current value as the person would read it ("Groq", "1080p", "on").
    static func display(_ setting: AgentSetting, _ value: String) -> String {
        switch setting.kind {
        case .choice(let options): return options.first { $0.value == value }?.label ?? value
        case .toggle, .number, .model, .text: return value.isEmpty ? "not set" : value
        }
    }

    /// What values are valid, in the model's terms (the option VALUES).
    static func optionsText(_ setting: AgentSetting) -> String {
        switch setting.kind {
        case .choice(let options): return options.map { $0.value }.joined(separator: " | ")
        case .toggle: return "on | off"
        case .number(let r): return "\(r.lowerBound)-\(r.upperBound)"
        case .model: return "a model id of the current service"
        case .text(let max): return "text, up to \(max) characters"
        }
    }

    /// One group's settings with current values and valid options - compact,
    /// since the model reads it as a tool result.
    static func describe(_ group: AgentSetting.Group, settings: AppSettings) -> String {
        Self.all.filter { $0.group == group }.map { item in
            let now = item.read(settings)
            return "\(item.key) = \(now.isEmpty ? "(none)" : now) - \(item.title). Values: \(optionsText(item))"
        }.joined(separator: "\n")
    }

    // MARK: Validating and applying

    /// The canonical form of what the model wrote, or nil if it is not valid
    /// for this setting. Forgiving about case, spacing and labels ("Night",
    /// "60fps", "1080p", "yes").
    static func canonical(_ raw: String, for setting: AgentSetting) -> String? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        func norm(_ s: String) -> String { s.lowercased().filter { !$0.isWhitespace } }
        switch setting.kind {
        case .choice(let options):
            let v = norm(trimmed)
            guard !v.isEmpty else { return nil }
            let exact = options.filter { norm($0.value) == v || norm($0.label) == v }
            if exact.count == 1 { return exact[0].value }
            // First word of the label ("night" for "Night", "apple" for "Apple voices (on-device)").
            let firstWord = options.filter { $0.label.lowercased().split(whereSeparator: { $0 == " " || $0 == "(" }).first.map(String.init) == v }
            return firstWord.count == 1 ? firstWord[0].value : nil
        case .toggle:
            switch norm(trimmed) {
            case "on", "true", "yes", "1", "enable", "enabled": return "on"
            case "off", "false", "no", "0", "disable", "disabled": return "off"
            default: return nil
            }
        case .number(let range):
            guard let digits = trimmed.split(whereSeparator: { !$0.isNumber }).first, let n = Int(digits),
                  range.contains(n) else { return nil }
            return String(n)
        case .model:
            guard !trimmed.isEmpty, trimmed.count <= 100, !trimmed.contains(where: { $0.isWhitespace }) else { return nil }
            return trimmed
        case .text(let max):
            guard !trimmed.isEmpty, trimmed.count <= max else { return nil }
            return trimmed
        }
    }

    /// Validates and applies one change to `settings`. The error is written
    /// for the model: it names the valid values so the next call can be right.
    static func apply(key: String, value raw: String, to settings: inout AppSettings) -> Result<AgentSettingChange, AgentToolError> {
        guard let item = setting(key) else {
            return .failure(AgentToolError(text: "Unknown setting “\(key)”. Valid: \(keys.joined(separator: ", "))."))
        }
        guard let value = canonical(raw, for: item) else {
            return .failure(AgentToolError(text: "“\(raw)” is not valid for \(item.key). Values: \(optionsText(item))."))
        }
        let before = item.read(settings)
        guard item.write(&settings, value) else {
            return .failure(AgentToolError(text: "\(item.title) does not apply with the current choices (for example a model id while the writer is Apple Intelligence)."))
        }
        return .success(AgentSettingChange(title: item.title, from: display(item, before), to: display(item, value)))
    }

    /// What `apply` would do, without doing it (for the approval card).
    static func preview(key: String, value raw: String, settings: AppSettings) -> Result<AgentSettingChange, AgentToolError> {
        var copy = settings
        return apply(key: key, value: raw, to: &copy)
    }
}
