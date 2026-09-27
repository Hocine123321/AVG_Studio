# AVG Studio for iPhone (native, iOS 26)

SwiftUI rewrite of AVG Studio that renders on-device. Liquid Glass UI.

| PC (Python) | iPhone (Swift) |
|---|---|
| pipeline/phases/writing.py | Core/Pipeline.swift `writing()` — Apple Foundation Models, Gemini, Groq, Pollinations |
| pipeline/phases/visuals.py | `visuals()` — Pollinations, Gemini, OpenAI (falls back to Pollinations) |
| pipeline/phases/voice.py | `voice()` — AVSpeechSynthesizer (on-device) or ElevenLabs |
| pipeline/phases/captions.py | `captions()` — estimated word timing |
| media/vendor/avg.py (ffmpeg) | Render/VideoRenderer.swift — Core Graphics frames + AVAssetWriter HEVC + AVFoundation mux |

Build: push to `ios-native`; GitHub Actions produces `AVGStudio-unsigned.ipa` (artifact). Sideload with Sideloadly or AltStore.
Local: `brew install xcodegen && cd ios && xcodegen generate && open AVGStudio.xcodeproj`.
