import Foundation
import AVFoundation
import UIKit
import CoreVideo
import CoreImage
import ImageIO
import SwiftUI
import Metal

struct CaptionStyle {
    var enabled: Bool
    var fontSize: CGFloat
    var position: CaptionPosition
    var animation: CaptionAnimationStyle = .fade
    /// Highlight colour - captions' own fill AND a card's rule, matching
    /// the PC's `overlay_highlight` (shared role, one setting).
    var accent: Color = Theme.accent
    /// Mirrors the PC's `overlay_accent1`/`overlay_accent2` - card-only,
    /// captions never read these.
    var accent1: Color = Theme.accent
    var accent2: Color = Theme.accent
    /// Mirrors `font_family` - the one font shared by captions and cards.
    /// Empty uses the system font.
    var fontFamily: String = ""
    /// How captions and cards are drawn (outline, plate, colours, card look).
    var overlay = OverlayStyle()
}

/// One UIFont for `family`/`size`/`weight`, falling back to the system font
/// (optionally San Francisco Rounded, for captions' own look) when `family`
/// is empty or names a font not actually installed - matching the PC's own
/// "empty/unrecognised uses the drawing library's own default" leniency
/// rather than a hard failure over a bad font name in a channel document.
private func themedFont(family: String, size: CGFloat, weight: UIFont.Weight, rounded: Bool = false) -> UIFont {
    if !family.isEmpty, let named = UIFont(name: family, size: size) { return named }
    let base = UIFont.systemFont(ofSize: size, weight: weight)
    if rounded { return base.fontDescriptor.withDesign(.rounded).map { UIFont(descriptor: $0, size: size) } ?? base }
    return base
}

/// A whole-video look filter, alpha-blended over every frame at
/// `intensity/100` opacity - the on-device analog of the PC's vendored
/// `film_grain.mov`/`light_leak.mov` overlay compositing. See
/// `FilterStyle`'s doc comment in Models.swift for what's deliberately
/// different about this version.
struct FilterSpec {
    var style: FilterStyle
    var intensity: Double
}

struct RenderInput {
    var images: [URL?]
    var sceneDurations: [Double]
    var audio: URL
    var cues: [CaptionCue]
    var size: CGSize
    var fps: Int
    var kenBurns: Bool
    var kbIntensity: Double
    /// The Director II AI plan, one entry per scene in `images`/
    /// `sceneDurations` order - see `Pipeline.planDirection()`. Empty means
    /// "not planned" and `VideoRenderer` falls back to its own
    /// deterministic Ken Burns/crossfade heuristic, exactly as before this
    /// feature existed.
    var sceneDirections: [SceneDirection] = []
    var gradeLook: GradeLook = .none
    var captions: CaptionStyle
    var filter: FilterSpec = FilterSpec(style: .none, intensity: 0)
    var cards: [TakeoverCard] = []
    var output: URL
    // Performance (Settings -> Performance). Defaults reproduce the old behaviour.
    var codec: VideoCodec = .hevc
    var encoderQuality: EncoderQuality = .automatic
    var engine: RenderEngine = .auto
    /// Wait for the device to cool when it reports serious heat.
    var pauseWhenHot = true
}

/// One scene image placed on the canvas for one frame.
struct ScenePlacement {
    let index: Int
    /// Destination rect in the canvas's bottom-left-origin coordinates.
    let rect: CGRect
    let alpha: Double
}

/// What a single output frame contains, decided ONCE from the timeline and
/// shared by both drawing back ends (GPU and CPU) - so they can never
/// disagree about which scene, which motion or which transition a frame is.
struct FramePlan {
    /// A takeover card owns the whole frame when present.
    var card: TakeoverCard?
    /// Scene layers, bottom first.
    var layers: [ScenePlacement]
    /// Black overlay opacity for a fade-in entrance.
    var fadeToBlack: CGFloat
}

/// Replaces the PC's ffmpeg engine: frames are composited and encoded by the
/// iPhone's hardware HEVC encoder, then muxed with the narration.
///
/// SPEED (a 30 s 720p video used to take 10+ minutes). The old loop drew
/// every frame in software with Core Graphics: a full 720x1280 resample of
/// the scene image, a soft-light or screen blend over the whole frame for
/// the film filter, and a blurred-shadow text draw for captions - all on one
/// CPU thread, ~900 times. This version:
///
///  * composites on the GPU (Core Image on Metal): the scene move, crossfade,
///    filter and caption overlay become a few texture operations per frame;
///  * prepares each scene image ONCE, converted to the exact pixel format
///    and colour space of the output, so nothing is colour-converted per
///    frame;
///  * draws captions and takeover cards to a bitmap only when their look
///    changes (per cue, or per word state), not 30 times a second;
///  * feeds the encoder from `requestMediaDataWhenReady`'s own queue instead
///    of polling with sleeps;
///  * falls back to the old Core Graphics path, frame by frame, whenever the
///    app is not allowed to use the GPU (background) or the GPU errors.
///
/// Set the launch argument `-avg.render.forceCPU YES` to force the old path
/// (for A/B timing or if a device misbehaves).
final class VideoRenderer {
    private let crossfade: Double = 0.35
    private lazy var grainTile: CGImage? = Self.makeGrainTile()
    /// Used only for the one-time grade bake of each source image.
    private lazy var ciContext = CIContext(options: [.useSoftwareRenderer: !AppActivity.shared.isActive])
    /// Every frame buffer AND every prepared source image uses this one
    /// space and pixel layout, so a Core Graphics draw is a straight copy
    /// with no per-pixel colour conversion.
    private let rgbColorSpace: CGColorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
    private static let bitmapInfo = CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue
    /// In-progress highlight words are drawn in this many colour steps, so a
    /// caption bitmap can be reused across several frames.
    private static let highlightSteps = 5

    // MARK: - Preview

    /// A video prepared once and then asked for any frame: the editor's scrub
    /// bar. It draws with the same `plan` and the same CPU `draw` as a real
    /// render (the GPU path is built to match it), so what it shows is what the
    /// render will produce. Decoding and grading every image is the slow part,
    /// so that happens once, in `init`; a frame is then a single draw.
    ///
    /// Not thread-safe: use it from one queue at a time.
    final class Preview: @unchecked Sendable {
        private let renderer: VideoRenderer
        private var input: RenderInput
        private let images: [CGImage?]
        private let sizes: [CGSize?]
        private let starts: [Double]
        private let canvas: CGRect
        /// Length of the video in seconds.
        let total: Double

        init(_ input: RenderInput) {
            let r = VideoRenderer()
            renderer = r
            self.input = input
            let w = Int(input.size.width), h = Int(input.size.height)
            canvas = CGRect(x: 0, y: 0, width: w, height: h)
            let prepared = r.prepareImages(input)
            images = prepared
            sizes = prepared.map { $0.map { CGSize(width: $0.width, height: $0.height) } }
            var acc = 0.0
            var begins: [Double] = []
            for d in input.sceneDurations { begins.append(acc); acc += d }
            starts = begins
            total = max(acc, 0.5)
        }

        /// New cards or captions (the editor changed them); the images stay.
        func update(cards: [TakeoverCard], cues: [CaptionCue]) {
            input.cards = cards
            input.cues = cues
        }

        /// The finished frame at `time` seconds, or nil if it could not be drawn.
        func frame(at time: Double) -> CGImage? {
            let t = min(max(0, time), max(0, total - 0.001))
            let w = Int(canvas.width), h = Int(canvas.height)
            var made: CVPixelBuffer?
            let attributes = [kCVPixelBufferCGImageCompatibilityKey: true,
                              kCVPixelBufferCGBitmapContextCompatibilityKey: true] as CFDictionary
            guard CVPixelBufferCreate(nil, w, h, kCVPixelFormatType_32BGRA, attributes, &made) == kCVReturnSuccess,
                  let buffer = made else { return nil }
            let framePlan = renderer.plan(t: t, input: input, starts: starts, sizes: sizes, canvas: canvas)
            renderer.draw(into: buffer, t: t, input: input, images: images, plan: framePlan)
            CVPixelBufferLockBaseAddress(buffer, .readOnly)
            defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
            guard let base = CVPixelBufferGetBaseAddress(buffer),
                  let ctx = CGContext(data: base, width: w, height: h, bitsPerComponent: 8,
                                      bytesPerRow: CVPixelBufferGetBytesPerRow(buffer),
                                      space: renderer.rgbColorSpace, bitmapInfo: VideoRenderer.bitmapInfo)
            else { return nil }
            return ctx.makeImage()
        }
    }

    // MARK: - Render

    @discardableResult
    func render(_ input: RenderInput, status: @escaping (String) -> Void = { _ in }, progress: @escaping (Double) -> Void) async throws -> RenderStats {
        let clock = ContinuousClock()
        var stats = RenderStats()
        let prepareStart = clock.now
        let fm = FileManager.default
        let folder = input.output.deletingLastPathComponent()
        let tmp = folder.appendingPathComponent("video_only.mp4")
        try? fm.removeItem(at: tmp)
        try? fm.removeItem(at: input.output)

        let w = Int(input.size.width), h = Int(input.size.height)
        let canvas = CGRect(x: 0, y: 0, width: w, height: h)

        // Decode, grade and normalise every source image once, up front.
        let images = prepareImages(input)
        let sizes: [CGSize?] = images.map { $0.map { CGSize(width: $0.width, height: $0.height) } }
        var starts: [Double] = []
        var acc = 0.0
        for d in input.sceneDurations { starts.append(acc); acc += d }
        let total = max(acc, 0.5)
        let frames = max(1, Int(total * Double(input.fps)))

        let gpu = input.engine == .cpu ? nil : makeGPUState(images: images, canvas: canvas)

        let writer = try AVAssetWriter(outputURL: tmp, fileType: .mp4)
        var compression: [String: Any] = [
            // Stills with slow camera moves gain nothing from B-frames,
            // and turning reordering off makes the hardware encoder's
            // job simpler and lower-latency.
            AVVideoAllowFrameReorderingKey: false,
            AVVideoExpectedSourceFrameRateKey: input.fps,
        ]
        // Automatic leaves the rate to the system (what the app always did);
        // the other quality choices ask for a specific average bitrate.
        if let bitrate = input.encoderQuality.bitrate(width: w, height: h, fps: input.fps, codec: input.codec) {
            compression[AVVideoAverageBitRateKey] = bitrate
        }
        if input.codec == .h264 { compression[AVVideoProfileLevelKey] = AVVideoProfileLevelH264HighAutoLevel }
        let vIn = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: input.codec == .hevc ? AVVideoCodecType.hevc : AVVideoCodecType.h264,
            AVVideoWidthKey: w,
            AVVideoHeightKey: h,
            AVVideoCompressionPropertiesKey: compression,
        ])
        vIn.expectsMediaDataInRealTime = false
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: vIn, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: w,
            kCVPixelBufferHeightKey as String: h,
            // IOSurface-backed and Metal-compatible so Core Image can render
            // straight into the encoder's own buffers with no copy.
            kCVPixelBufferIOSurfacePropertiesKey as String: [String: Any](),
            kCVPixelBufferMetalCompatibilityKey as String: true,
        ])
        writer.add(vIn)
        guard writer.startWriting() else { throw writer.error ?? AVGError.message("Couldn't start the video encoder.") }
        writer.startSession(atSourceTime: .zero)
        stats.prepareSeconds = (clock.now - prepareStart).seconds

        // MARK: Frame loop
        //
        // The encoder pulls frames: `requestMediaDataWhenReady` calls back on
        // our own serial queue whenever it can take more, and we produce
        // frames until it says stop. No sleeping, no per-frame task hops.
        let loopStart = clock.now
        let cancel = CancelFlag()
        let counters = FrameCounters()
        let fps = input.fps
        let queue = DispatchQueue(label: "com.avgstudio.render.frames", qos: .userInitiated)

        do {
            try await withTaskCancellationHandler {
                try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
                    vIn.requestMediaDataWhenReady(on: queue) { [self] in
                        while vIn.isReadyForMoreMediaData, !counters.finished {
                            if cancel.isRaised {
                                counters.finished = true
                                vIn.markAsFinished()
                                cont.resume(throwing: CancellationError())
                                return
                            }
                            let f = counters.frame
                            // Every half second of video, check the phone isn't
                            // overheating; if it is, wait for it to cool (a hot
                            // iPhone throttles hard and a long render only
                            // makes it worse).
                            if input.pauseWhenHot, f % max(1, fps / 2) == 0 {
                                Self.coolDownIfNeeded(cancel: cancel, status: status)
                            }
                            if f >= frames {
                                counters.finished = true
                                vIn.markAsFinished()
                                cont.resume()
                                return
                            }
                            do {
                                try autoreleasepool {
                                    guard let pool = adaptor.pixelBufferPool else { throw AVGError.message("Encoder pool unavailable.") }
                                    var pbOut: CVPixelBuffer?
                                    CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pbOut)
                                    guard let pb = pbOut else { throw AVGError.message("Out of memory while rendering.") }
                                    let t = Double(f) / Double(fps)
                                    let plan = self.plan(t: t, input: input, starts: starts, sizes: sizes, canvas: canvas)
                                    var onGPU = false
                                    if let gpu, AppActivity.shared.isActive {
                                        let image = self.composeGPU(plan: plan, t: t, input: input, gpu: gpu)
                                        do {
                                            let task = try gpu.context.startTask(toRender: image, to: CIRenderDestination(pixelBuffer: pb))
                                            _ = try task.waitUntilCompleted()
                                            onGPU = true
                                        } catch {
                                            // GPU refused or failed: draw THIS frame on the CPU.
                                        }
                                    }
                                    if onGPU { counters.gpuFrames += 1 }
                                    else { self.draw(into: pb, t: t, input: input, images: images, plan: plan) }
                                    if !adaptor.append(pb, withPresentationTime: CMTime(value: Int64(f), timescale: Int32(fps))) {
                                        throw writer.error ?? AVGError.message("The encoder rejected a frame.")
                                    }
                                }
                            } catch {
                                counters.finished = true
                                vIn.markAsFinished()
                                cont.resume(throwing: error)
                                return
                            }
                            counters.frame = f + 1
                            if f % 15 == 0 { progress(Double(f) / Double(frames) * 0.85) }
                        }
                    }
                }
            } onCancel: {
                cancel.raise()
            }
        } catch {
            writer.cancelWriting() // also removes the half-written file
            throw error
        }
        await writer.finishWriting()
        if writer.status != .completed { throw writer.error ?? AVGError.message("Video encoding failed.") }
        stats.frames = counters.frame
        stats.gpuFrames = counters.gpuFrames
        stats.frameSeconds = (clock.now - loopStart).seconds

        // MARK: Mux
        let muxStart = clock.now
        let comp = AVMutableComposition()
        let va = AVURLAsset(url: tmp)
        let aa = AVURLAsset(url: input.audio)
        guard let vt = try await va.loadTracks(withMediaType: .video).first,
              let cv = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid) else {
            throw AVGError.message("Rendered video is empty.")
        }
        let vdur = try await va.load(.duration)
        try cv.insertTimeRange(CMTimeRange(start: .zero, duration: vdur), of: vt, at: .zero)
        if let at = try await aa.loadTracks(withMediaType: .audio).first,
           let ca = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) {
            let adur = try await aa.load(.duration)
            try ca.insertTimeRange(CMTimeRange(start: .zero, duration: CMTimeMinimum(adur, vdur)), of: at, at: .zero)
        }
        // The video is already HEVC and the narration is AAC in .m4a, so
        // passthrough keeps both streams intact with no second encode.
        guard let export = AVAssetExportSession(asset: comp, presetName: AVAssetExportPresetPassthrough) else {
            throw AVGError.message("Couldn't create the passthrough exporter.")
        }
        progress(0.9)
        try await export.export(to: input.output, as: .mp4)
        try? fm.removeItem(at: tmp)
        stats.muxSeconds = (clock.now - muxStart).seconds
        progress(1)
        return stats
    }

    /// Blocks (on the render queue, which has nothing else to do) while the
    /// device reports serious or critical heat, up to two minutes at a time,
    /// then carries on regardless - slow is better than never finishing.
    static func coolDownIfNeeded(cancel: CancelFlag, status: (String) -> Void,
                                 state: () -> ProcessInfo.ThermalState = { ProcessInfo.processInfo.thermalState },
                                 sleep: (TimeInterval) -> Void = { Thread.sleep(forTimeInterval: $0) },
                                 limit: TimeInterval = 120) {
        var waited = 0.0
        while state().rawValue >= ProcessInfo.ThermalState.serious.rawValue, !cancel.isRaised, waited < limit {
            if waited == 0 { status("Letting the iPhone cool down…") }
            sleep(1)
            waited += 1
        }
        if waited > 0 { status("Rendering frames…") }
    }

    /// Mutable counters shared between the render queue and `render`. Only
    /// the render queue writes them while frames are being made.
    private final class FrameCounters: @unchecked Sendable {
        var frame = 0
        var gpuFrames = 0
        var finished = false
    }

    // MARK: - Timeline

    /// Decides what frame `t` contains. Pure: no drawing, no state.
    func plan(t: Double, input: RenderInput, starts: [Double], sizes: [CGSize?], canvas: CGRect) -> FramePlan {
        // A takeover card OWNS its whole scene window - no scene image,
        // no Ken Burns, no crossfade, no captions underneath it, cutting
        // in/out instantly. Mirrors overlay_design.py's own "every card
        // renders full-bleed/opaque... not floating over footage".
        if let card = input.cards.first(where: { t >= $0.start && t < $0.end }) {
            return FramePlan(card: card, layers: [], fadeToBlack: 0)
        }

        var idx = 0
        for (i, s) in starts.enumerated() where t >= s { idx = i }
        let dur = input.sceneDurations.indices.contains(idx) ? input.sceneDurations[idx] : 1
        let sceneStart = starts[safe: idx, default: 0]
        let local = min(max((t - sceneStart) / max(dur, 0.01), 0), 1)
        let direction = input.sceneDirections.indices.contains(idx) ? input.sceneDirections[idx] : nil

        var layers: [ScenePlacement] = []
        var fade: CGFloat = 0

        if let direction {
            // The Director II plan owns BOTH motion and this scene's own
            // ENTRANCE transition - ported from `avg.py`'s
            // `_apply_scene_transition` (a fade-up-from-black or a
            // slide-into-place on the entering clip's own opening frames,
            // never a blend with the previous clip - see `SceneTransition`'s
            // doc comment for why "crossfade" behaves like "fade" here).
            var extraDX: CGFloat = 0
            let d = min(crossfade, dur * 0.5)
            let elapsed = t - sceneStart
            if d > 0, elapsed < d {
                let tp = elapsed / d
                switch direction.transitionIn {
                case .cut:
                    break
                case .fade, .crossfade, .dip:
                    let f = 1 - pow(1 - tp, 2)
                    fade = CGFloat(1 - f)
                case .slideLeft, .whip:
                    extraDX = -CGFloat(1 - tp) * canvas.width
                case .slideRight:
                    extraDX = CGFloat(1 - tp) * canvas.width
                }
            }
            if let size = sizes[safe: idx, default: nil] {
                let rect = Self.placement(size: size, canvas: canvas, sceneIndex: idx, p: local, input: input, motion: direction, extraDX: extraDX)
                layers.append(ScenePlacement(index: idx, rect: rect, alpha: 1))
            }
        } else {
            // No AI plan (planning never ran, or failed) - the original
            // deterministic heuristic: a fixed Ken Burns pattern by scene
            // index, and a fixed crossfade into the next scene's opening.
            if let size = sizes[safe: idx, default: nil] {
                let rect = Self.placement(size: size, canvas: canvas, sceneIndex: idx, p: local, input: input, motion: nil)
                layers.append(ScenePlacement(index: idx, rect: rect, alpha: 1))
            }
            let remaining = sceneStart + dur - t
            if remaining < crossfade, idx + 1 < sizes.count, let size = sizes[idx + 1] {
                let a = 1 - remaining / crossfade
                let rect = Self.placement(size: size, canvas: canvas, sceneIndex: idx + 1, p: 0, input: input, motion: nil)
                layers.append(ScenePlacement(index: idx + 1, rect: rect, alpha: a))
            }
        }
        return FramePlan(card: nil, layers: layers, fadeToBlack: fade)
    }

    // MARK: - CPU frame drawing (Core Graphics)
    //
    // The original renderer, now driven by a `FramePlan`. Used when the GPU
    // is unavailable (app in the background) or fails.

    private func draw(into pb: CVPixelBuffer, t: Double, input: RenderInput, images: [CGImage?], plan: FramePlan) {
        CVPixelBufferLockBaseAddress(pb, [])
        defer { CVPixelBufferUnlockBaseAddress(pb, []) }
        let w = CVPixelBufferGetWidth(pb), h = CVPixelBufferGetHeight(pb)
        guard let ctx = CGContext(data: CVPixelBufferGetBaseAddress(pb), width: w, height: h, bitsPerComponent: 8,
                                  bytesPerRow: CVPixelBufferGetBytesPerRow(pb), space: rgbColorSpace,
                                  bitmapInfo: Self.bitmapInfo)
        else { return }
        let canvas = CGRect(x: 0, y: 0, width: w, height: h)
        ctx.setFillColor(UIColor.black.cgColor)
        ctx.fill(canvas)
        ctx.interpolationQuality = .high

        if let card = plan.card {
            // The entrance (fade / pop) is a transform of the whole card about
            // the frame's centre; the black behind it shows through while it
            // is partly transparent or smaller.
            let motion = Self.cardMotion(card, t: t, style: input.captions.overlay)
            ctx.saveGState()
            ctx.setAlpha(motion.alpha)
            ctx.translateBy(x: canvas.midX, y: canvas.midY)
            ctx.scaleBy(x: motion.scale, y: motion.scale)
            ctx.translateBy(x: -canvas.midX, y: -canvas.midY)
            drawCard(ctx, canvas: canvas, card: card, accent1: UIColor(input.captions.accent1), accent2: UIColor(input.captions.accent2), fontFamily: input.captions.fontFamily, style: input.captions.overlay)
            ctx.restoreGState()
            applyFilter(ctx, canvas: canvas, t: t, filter: input.filter)
            return
        }

        for layer in plan.layers {
            guard let image = images[safe: layer.index, default: nil] else { continue }
            ctx.saveGState()
            ctx.setAlpha(CGFloat(layer.alpha))
            ctx.draw(image, in: layer.rect)
            ctx.restoreGState()
        }
        if plan.fadeToBlack > 0 {
            ctx.saveGState()
            ctx.setFillColor(UIColor.black.withAlphaComponent(plan.fadeToBlack).cgColor)
            ctx.fill(canvas)
            ctx.restoreGState()
        }

        // The constant global grade was baked into each source image once in
        // render() (ported from `avg.py`'s `_apply_grade`). Filter overlay
        // sits under captions, same as the PC's own pass ordering, so
        // grain/light leak never fights caption legibility.
        applyFilter(ctx, canvas: canvas, t: t, filter: input.filter)

        if input.captions.enabled, let cue = input.cues.first(where: { t >= $0.start && t < $0.end }) {
            drawCaption(ctx, canvas: canvas, cue: cue, t: t, style: input.captions)
        }
    }

    // MARK: - GPU frame composition (Core Image / Metal)

    /// Everything the GPU path keeps alive for the length of one render.
    private final class GPUState {
        let context: CIContext
        let canvas: CGRect
        let black: CIImage
        /// One per scene image - kept as long-lived objects so Core Image
        /// uploads each texture once and reuses it on every frame.
        let sources: [CIImage?]
        /// Film-grain tile repeated to (canvas + one tile), built once.
        let grain: CIImage?
        var cards: [String: CIImage] = [:]
        var caption: (key: CaptionKey, overlay: CaptionOverlay)?

        init(context: CIContext, canvas: CGRect, sources: [CIImage?], grain: CIImage?) {
            self.context = context
            self.canvas = canvas
            self.black = CIImage(color: CIColor.black).cropped(to: canvas)
            self.sources = sources
            self.grain = grain
        }
    }

    private struct CaptionKey: Hashable {
        let start: Double
        let text: String
        let signature: Int
    }

    private struct CaptionOverlay {
        let image: CIImage
        let rect: CGRect
    }

    private func makeGPUState(images: [CGImage?], canvas: CGRect) -> GPUState? {
        if UserDefaults.standard.bool(forKey: "avg.render.forceCPU") { return nil }
        guard let device = MTLCreateSystemDefaultDevice() else { return nil }
        // Colour management OFF. Core Image's default works in linear light
        // and converts in and out of it; Core Graphics (the original path and
        // the CPU fallback) blends the sRGB numbers directly. Matching that
        // keeps crossfades and the film filter looking the same on both paths
        // and skips two conversions per pixel. Every input is already sRGB.
        let ctx = CIContext(mtlDevice: device, options: [
            .workingColorSpace: NSNull(),
            .outputColorSpace: NSNull(),
        ])
        let sources: [CIImage?] = images.map { $0.map { CIImage(cgImage: $0) } }
        var grain: CIImage?
        if let tile = grainTile {
            let side = CGFloat(tile.width)
            grain = CIImage(cgImage: tile)
                .applyingFilter("CIAffineTile")
                .cropped(to: CGRect(x: 0, y: 0, width: canvas.width + side, height: canvas.height + side))
        }
        return GPUState(context: ctx, canvas: canvas, sources: sources, grain: grain)
    }

    private func composeGPU(plan: FramePlan, t: Double, input: RenderInput, gpu: GPUState) -> CIImage {
        let canvas = gpu.canvas

        if let card = plan.card {
            var base = cardImage(card, input: input, gpu: gpu) ?? gpu.black
            let motion = Self.cardMotion(card, t: t, style: input.captions.overlay)
            if motion.scale != 1 {
                base = base.transformed(by: CGAffineTransform(translationX: -canvas.midX, y: -canvas.midY)
                    .concatenating(CGAffineTransform(scaleX: motion.scale, y: motion.scale))
                    .concatenating(CGAffineTransform(translationX: canvas.midX, y: canvas.midY)))
            }
            if motion.alpha < 1 {
                base = base.applyingFilter("CIColorMatrix", parameters: ["inputAVector": CIVector(x: 0, y: 0, z: 0, w: motion.alpha)])
            }
            let framed = base.composited(over: gpu.black)
            return applyFilterCI(framed, t: t, filter: input.filter, gpu: gpu).cropped(to: canvas)
        }

        var img = gpu.black
        for layer in plan.layers {
            guard let src = gpu.sources[safe: layer.index, default: nil] else { continue }
            let e = src.extent
            guard e.width > 0, e.height > 0 else { continue }
            var moved = src.transformed(by: CGAffineTransform(
                a: layer.rect.width / e.width, b: 0, c: 0, d: layer.rect.height / e.height,
                tx: layer.rect.minX, ty: layer.rect.minY))
            if layer.alpha < 1 {
                moved = moved.applyingFilter("CIColorMatrix", parameters: [
                    "inputAVector": CIVector(x: 0, y: 0, z: 0, w: CGFloat(layer.alpha)),
                ])
            }
            img = moved.composited(over: img)
        }
        if plan.fadeToBlack > 0 {
            img = CIImage(color: CIColor(red: 0, green: 0, blue: 0, alpha: plan.fadeToBlack))
                .cropped(to: canvas)
                .composited(over: img)
        }

        img = applyFilterCI(img, t: t, filter: input.filter, gpu: gpu)

        if input.captions.enabled, let cue = input.cues.first(where: { t >= $0.start && t < $0.end }) {
            let key = CaptionKey(start: cue.start, text: cue.text, signature: captionSignature(cue: cue, t: t, style: input.captions))
            var overlay: CaptionOverlay?
            if let cached = gpu.caption, cached.key == key {
                overlay = cached.overlay
            } else if let built = buildCaptionOverlay(cue: cue, t: t, style: input.captions, canvas: canvas) {
                gpu.caption = (key, built)
                overlay = built
            }
            if let overlay {
                let motion = Self.captionMotion(cue: cue, t: t, style: input.captions, canvas: canvas)
                var ci = overlay.image
                if motion.scale != 1 || motion.offsetY != 0 {
                    ci = ci.transformed(by: Self.captionTransform(rect: overlay.rect, canvas: canvas, motion: motion))
                }
                if motion.alpha < 1 {
                    ci = ci.applyingFilter("CIColorMatrix", parameters: [
                        "inputAVector": CIVector(x: 0, y: 0, z: 0, w: motion.alpha),
                    ])
                }
                img = ci.composited(over: img)
            }
        }
        return img.cropped(to: canvas)
    }

    /// A takeover card is static for its whole window: draw it once with
    /// Core Graphics, then reuse the bitmap for every frame.
    private func cardImage(_ card: TakeoverCard, input: RenderInput, gpu: GPUState) -> CIImage? {
        if let hit = gpu.cards[card.id] { return hit }
        guard let ctx = makeContext(Int(gpu.canvas.width), Int(gpu.canvas.height)) else { return nil }
        drawCard(ctx, canvas: gpu.canvas, card: card, accent1: UIColor(input.captions.accent1), accent2: UIColor(input.captions.accent2), fontFamily: input.captions.fontFamily, style: input.captions.overlay)
        guard let cg = ctx.makeImage() else { return nil }
        let image = CIImage(cgImage: cg)
        gpu.cards[card.id] = image
        return image
    }

    /// GPU twin of `applyFilter`: same looks, same timing formulas.
    private func applyFilterCI(_ base: CIImage, t: Double, filter: FilterSpec, gpu: GPUState) -> CIImage {
        guard filter.style != .none, filter.intensity > 0 else { return base }
        let canvas = gpu.canvas
        let amount = CGFloat(filter.intensity / 100.0)
        switch filter.style {
        case .none:
            return base
        case .filmGrain:
            guard let grain = gpu.grain else { return base }
            // Same cheap deterministic "flicker" as the CPU path: slide the
            // tile origin every frame instead of regenerating noise.
            let tileSize = 128
            let jx = CGFloat(Int(t * 24) % tileSize)
            let jy = CGFloat(Int(t * 17) % tileSize)
            let overlay = grain
                .transformed(by: CGAffineTransform(translationX: -jx, y: -jy))
                .cropped(to: canvas)
                .applyingFilter("CIColorMatrix", parameters: [
                    "inputAVector": CIVector(x: 0, y: 0, z: 0, w: min(0.45, amount * 0.5)),
                ])
            return overlay.applyingFilter("CISoftLightBlendMode", parameters: [kCIInputBackgroundImageKey: base])
        case .lightLeak:
            let cycle = 6.0
            let phase = (t.truncatingRemainder(dividingBy: cycle)) / cycle
            let cx = canvas.width * CGFloat(-0.3 + 1.6 * phase)
            let cy = canvas.height * 0.22
            let radius = max(canvas.width, canvas.height) * 0.65
            guard let leak = CIFilter(name: "CIRadialGradient", parameters: [
                "inputCenter": CIVector(x: cx, y: cy),
                "inputRadius0": 0,
                "inputRadius1": radius,
                "inputColor0": CIColor(red: 1.0, green: 0.75, blue: 0.45, alpha: amount * 0.55),
                "inputColor1": CIColor(red: 1.0, green: 0.5, blue: 0.3, alpha: 0),
            ])?.outputImage else { return base }
            return leak.cropped(to: canvas)
                .applyingFilter("CIScreenBlendMode", parameters: [kCIInputBackgroundImageKey: base])
        }
    }

    // MARK: - Shared bitmap helpers

    /// A transparent bitmap context in the output's own pixel format.
    private func makeContext(_ w: Int, _ h: Int) -> CGContext? {
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: rgbColorSpace, bitmapInfo: Self.bitmapInfo) else { return nil }
        ctx.clear(CGRect(x: 0, y: 0, width: w, height: h))
        return ctx
    }

    // MARK: - Takeover cards
    //
    // Deterministic "Focused" full-screen cards - see `TakeoverCard`'s doc
    // comment in Models.swift. Auto-fits the font size down until the
    // question text fits in <= 2 lines within a safe box, exactly the loop
    // `overlay_design.py`'s `_fit_card_font` runs (no Pillow here, but the
    // same shrink-until-it-fits idea against Core Text metrics), then draws
    // the text centered with a small accent rule beneath it - the one
    // static-text composition every takeover card in the PC shares.

    /// The colours a card is drawn in, for the chosen background.
    static func cardPalette(_ style: OverlayStyle, accent1: UIColor) -> (background: UIColor, text: UIColor, subtext: UIColor) {
        switch style.cardBackground {
        case .dark:
            // overlay_design.py's DEFAULT_BRAND["bg"] (#0B0F1A).
            return (UIColor(red: 0x0B / 255.0, green: 0x0F / 255.0, blue: 0x1A / 255.0, alpha: 1),
                    UIColor(white: 0.95, alpha: 1), UIColor(white: 0.95, alpha: 0.72))
        case .light:
            return (UIColor(red: 0.957, green: 0.945, blue: 0.918, alpha: 1),
                    UIColor(red: 0.08, green: 0.09, blue: 0.11, alpha: 1), UIColor(red: 0.08, green: 0.09, blue: 0.11, alpha: 0.66))
        case .brand:
            var h: CGFloat = 0, sat: CGFloat = 0, br: CGFloat = 0, a: CGFloat = 0
            accent1.getHue(&h, saturation: &sat, brightness: &br, alpha: &a)
            return (UIColor(hue: h, saturation: min(1, sat * 0.9), brightness: 0.16, alpha: 1),
                    UIColor(white: 0.97, alpha: 1), UIColor(white: 0.97, alpha: 0.74))
        }
    }

    /// The entrance of a card as an opacity and a scale about the centre, by
    /// how long it has been on screen. Shared by both drawing paths.
    static func cardMotion(_ card: TakeoverCard, t: Double, style: OverlayStyle) -> (alpha: CGFloat, scale: CGFloat) {
        let age = max(0, t - card.start)
        switch style.cardAnimation {
        case .none: return (1, 1)
        case .fade: return (CGFloat(min(1, age / 0.18)), 1)
        case .pop:
            let p = min(1, age / 0.22)
            let eased = 1 - pow(1 - p, 3)
            return (CGFloat(min(1, age / 0.12)), CGFloat(0.94 + 0.06 * eased))
        }
    }

    private func drawCard(_ ctx: CGContext, canvas: CGRect, card: TakeoverCard, accent1: UIColor, accent2: UIColor, fontFamily: String, style: OverlayStyle) {
        ctx.saveGState()
        let palette = Self.cardPalette(style, accent1: accent1)
        // The card owns the frame, so this replaces the scene entirely.
        ctx.setFillColor(palette.background.cgColor)
        ctx.fill(canvas)

        // The whole card is laid out in TOP-DOWN coordinates (what text drawing
        // wants), so flip once up front. The stripe used to be drawn BEFORE this
        // flip, in bottom-up space - which put the "top edge" stripe on the
        // bottom edge; it now sits where its own comment always said.
        ctx.translateBy(x: 0, y: canvas.height)
        ctx.scaleBy(x: 1, y: -1)

        let shortEdge = min(canvas.width, canvas.height)
        let left = style.cardAlignment == .left
        if style.cardAccentBars {
            // A thin accent2 stripe along the top edge - the one place a card
            // uses the PC's "secondary" accent (overlay_accent2); accent1 stays
            // reserved for the rule beneath the headline.
            ctx.setFillColor(accent2.cgColor)
            ctx.fill(CGRect(x: 0, y: 0, width: canvas.width, height: max(3, canvas.height * 0.006)))
        }

        let boxW = canvas.width * 0.84
        let boxH = canvas.height * 0.72
        let boxX = left ? canvas.width * 0.08 : (canvas.width - boxW) / 2
        let floorSize = max(14, shortEdge * 0.035)
        let maxLines = 2 // Shared by Focused, Explainer and chapter cards - overlay_design.py draws them identically once picked; only WHO picks the text differs.
        let para = NSMutableParagraphStyle()
        para.alignment = left ? .left : .center
        para.lineBreakMode = .byWordWrapping

        func shown(_ text: String) -> String { style.cardUppercase ? text.uppercased() : text }
        let headline = shown(card.text)

        var size = shortEdge * 0.15 * CGFloat(min(1.6, max(0.5, style.cardTextScale)))
        var font = themedFont(family: fontFamily, size: size, weight: .heavy)
        while size > floorSize {
            font = themedFont(family: fontFamily, size: size, weight: .heavy)
            let probe = NSAttributedString(string: headline, attributes: [.font: font, .paragraphStyle: para])
            let bounds = probe.boundingRect(with: CGSize(width: boxW, height: .greatestFiniteMagnitude), options: [.usesLineFragmentOrigin], context: nil)
            let lines = max(1, Int((bounds.height / font.lineHeight).rounded(.up)))
            if bounds.height <= boxH, lines <= maxLines { break }
            size -= max(2, size * 0.06)
        }

        func block(_ text: String, font: UIFont, color: UIColor, kern: CGFloat = 0) -> (NSAttributedString, CGFloat) {
            let a = NSAttributedString(string: text, attributes: [.font: font, .paragraphStyle: para, .foregroundColor: color, .kern: kern])
            return (a, a.boundingRect(with: CGSize(width: boxW, height: boxH), options: [.usesLineFragmentOrigin], context: nil).height)
        }
        let gap = shortEdge * 0.025
        var blocks: [(NSAttributedString, CGFloat)] = []
        if card.isChapter, let eyebrow = card.eyebrow, !eyebrow.isEmpty {
            blocks.append(block(eyebrow.uppercased(), font: themedFont(family: fontFamily, size: max(floorSize, size * 0.30), weight: .bold), color: accent2, kern: size * 0.04))
        }
        blocks.append(block(headline, font: font, color: palette.text))
        if card.isChapter, let sub = card.subtitle, !sub.isEmpty {
            blocks.append(block(shown(sub), font: themedFont(family: fontFamily, size: max(floorSize, size * 0.42), weight: .medium), color: palette.subtext))
        }

        let totalH = blocks.reduce(0) { $0 + $1.1 } + gap * CGFloat(max(0, blocks.count - 1))
        var y = (canvas.height - totalH) / 2
        UIGraphicsPushContext(ctx)
        for (text, h) in blocks {
            text.draw(with: CGRect(x: boxX, y: y, width: boxW, height: h), options: [.usesLineFragmentOrigin], context: nil)
            y += h + gap
        }
        UIGraphicsPopContext()

        if style.cardAccentBars {
            // Accent rule beneath the text block - overlay_design.py's own
            // rounded "rule" divider under a card's headline.
            let ruleW = min(canvas.width * 0.22, shortEdge * 0.18)
            let ruleH = max(3, shortEdge * 0.006)
            let ruleRect = CGRect(x: left ? boxX : (canvas.width - ruleW) / 2, y: y - gap + shortEdge * 0.03, width: ruleW, height: ruleH)
            ctx.setFillColor(accent1.cgColor)
            ctx.addPath(UIBezierPath(roundedRect: ruleRect, cornerRadius: ruleH / 2).cgPath)
            ctx.fillPath()
        }
        ctx.restoreGState()
    }

    /// Where a scene image lands on the canvas for one frame - the motion
    /// maths that used to live inside `drawScene`, now shared by the GPU and
    /// CPU paths. `motion` is the Director II plan's entry for this scene
    /// (nil when no plan exists - see `RenderInput.sceneDirections`'s doc
    /// comment). `extraDX`/`extraDY` are an entrance-transition offset
    /// (slide/whip only), added on top of whatever the motion itself
    /// computes - the two are independent PC concerns (`_apply_ken_burns`
    /// vs `_apply_scene_transition`) composited exactly as there.
    static func placement(size: CGSize, canvas: CGRect, sceneIndex: Int, p: Double, input: RenderInput, motion: SceneDirection?, extraDX: CGFloat = 0, extraDY: CGFloat = 0) -> CGRect {
        let iw = max(size.width, 1), ih = max(size.height, 1)
        let fill = max(canvas.width / iw, canvas.height / ih)
        var scale = fill
        var dx: CGFloat = 0, dy: CGFloat = 0
        if input.kenBurns {
            if let motion {
                // Ported from `avg.py`'s `_apply_ken_burns`: an eased scale
                // (zoom) or an eased pan across the scene's own window,
                // sized so the oversize scale always leaves enough
                // headroom that a pan never exposes a black edge.
                let e = CGFloat(Self.ease(motion.ease, p))
                let amt = 0.12 * input.kbIntensity * motion.intensity
                switch motion.motionType {
                case .still:
                    break
                case .zoomIn:
                    scale = fill * CGFloat(1 + amt * Double(e))
                case .zoomOut:
                    scale = fill * CGFloat(1 + amt * Double(1 - e))
                case .panLeft, .panRight, .panUp, .panDown:
                    let panFrac = 0.5 * amt
                    scale = fill * CGFloat(1 + panFrac)
                    let horizontal = motion.motionType == .panLeft || motion.motionType == .panRight
                    let travel = CGFloat(panFrac) * (horizontal ? canvas.width : canvas.height)
                    let offset = travel * (e - 0.5)
                    switch motion.motionType {
                    case .panLeft: dx = offset
                    case .panRight: dx = -offset
                    case .panUp: dy = offset
                    case .panDown: dy = -offset
                    default: break
                    }
                }
            } else {
                // No AI plan - the original deterministic pattern.
                let amt = 0.12 * input.kbIntensity
                let e = CGFloat(p * p * (3 - 2 * p)) // smoothstep
                let zoomIn = sceneIndex % 2 == 0
                scale = fill * CGFloat(1 + amt * Double(zoomIn ? e : 1 - e))
                let pan = CGFloat(amt) * 0.5
                switch sceneIndex % 4 {
                case 1: dx = (e - 0.5) * pan * canvas.width
                case 3: dy = (e - 0.5) * pan * canvas.height
                default: break
                }
            }
        }
        let dw = iw * scale, dh = ih * scale
        return CGRect(x: (canvas.width - dw) / 2 + dx + extraDX, y: (canvas.height - dh) / 2 + dy + extraDY, width: dw, height: dh)
    }

    /// Ported from the PC's `_ease` (`avg/media/vendor/avg.py`) - the same
    /// five named curves over motion progress `t` (already scene-local,
    /// 0…1 across that scene's own window).
    private static func ease(_ curve: EaseCurve, _ t: Double) -> Double {
        let t = min(max(t, 0), 1)
        switch curve {
        case .linear: return t
        case .easeIn: return t * t
        case .easeOut: return 1 - (1 - t) * (1 - t)
        case .easeInOut: return t < 0.5 ? 2 * t * t : 1 - pow(-2 * t + 2, 2) / 2
        case .easeOutCubic: return 1 - pow(1 - t, 3)
        }
    }

    /// Decodes, grades and normalises every scene image ONCE.
    ///
    /// Normalising means redrawing into the output's exact pixel layout and
    /// colour space. Without it each per-frame draw (CPU path) or texture
    /// upload (GPU path) had to colour-convert a JPEG/Display-P3 source, and
    /// lazily-decoded images could be re-decoded under memory pressure.
    private func prepareImages(_ input: RenderInput) -> [CGImage?] {
        let motionHeadroom = (input.kenBurns || !input.sceneDirections.isEmpty) ? 1.25 : 1.0
        let maxPixelSize = max(1, Int(ceil(max(input.size.width, input.size.height) * motionHeadroom)))
        return input.images.map { url in
            guard let url, let raw = Self.downsampledImage(at: url, maxPixelSize: maxPixelSize) else { return nil }
            let graded = gradeImage(raw, look: input.gradeLook) ?? raw
            return normalised(graded) ?? graded
        }
    }

    private func normalised(_ image: CGImage) -> CGImage? {
        guard let ctx = makeContext(image.width, image.height) else { return nil }
        ctx.interpolationQuality = .none // 1:1 copy, no resampling
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        return ctx.makeImage()
    }

    private static func downsampledImage(at url: URL, maxPixelSize: Int) -> CGImage? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            // Decode NOW, here, instead of lazily on the first frame drawn.
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    }

    /// Bakes the constant whole-video look once per source image. This is the
    /// same optimization used by the legacy renderer's graded-image cache:
    /// motion still happens per frame, but the expensive color transform does
    /// not.
    private func gradeImage(_ image: CGImage, look: GradeLook) -> CGImage? {
        guard look != .none else { return image }
        let g = look.params
        let ci = CIImage(cgImage: image)
        guard let matrix = CIFilter(name: "CIColorMatrix") else { return image }
        matrix.setValue(ci, forKey: kCIInputImageKey)
        matrix.setValue(CIVector(x: CGFloat(g.r), y: 0, z: 0, w: 0), forKey: "inputRVector")
        matrix.setValue(CIVector(x: 0, y: CGFloat(g.g), z: 0, w: 0), forKey: "inputGVector")
        matrix.setValue(CIVector(x: 0, y: 0, z: CGFloat(g.b), w: 0), forKey: "inputBVector")
        matrix.setValue(CIVector(x: 0, y: 0, z: 0, w: 1), forKey: "inputAVector")
        guard let gained = matrix.outputImage, let controls = CIFilter(name: "CIColorControls") else { return image }
        controls.setValue(gained, forKey: kCIInputImageKey)
        controls.setValue(g.contrast, forKey: kCIInputContrastKey)
        controls.setValue(g.saturation, forKey: kCIInputSaturationKey)
        guard let output = controls.outputImage else { return image }
        return ciContext.createCGImage(output, from: ci.extent) ?? image
    }

    // MARK: - Filter overlay (procedural film grain / light leak)

    private static func makeGrainTile() -> CGImage? {
        let size = 128
        var pixels = [UInt8](repeating: 0, count: size * size * 4)
        for i in 0..<(size * size) {
            let v = UInt8.random(in: 90...165) // mid-gray noise: keeps a softLight blend subtle, not a exposure shift
            pixels[i * 4 + 0] = v; pixels[i * 4 + 1] = v; pixels[i * 4 + 2] = v; pixels[i * 4 + 3] = 255
        }
        guard let cs = CGColorSpace(name: CGColorSpace.sRGB) else { return nil }
        return pixels.withUnsafeMutableBytes { buf -> CGImage? in
            guard let ctx = CGContext(data: buf.baseAddress, width: size, height: size, bitsPerComponent: 8,
                                       bytesPerRow: size * 4, space: cs,
                                       bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
            return ctx.makeImage()
        }
    }

    private func applyFilter(_ ctx: CGContext, canvas: CGRect, t: Double, filter: FilterSpec) {
        guard filter.style != .none, filter.intensity > 0 else { return }
        let amount = CGFloat(filter.intensity / 100.0)
        ctx.saveGState()
        switch filter.style {
        case .none:
            break
        case .filmGrain:
            if let tile = grainTile {
                ctx.setAlpha(min(0.45, amount * 0.5))
                ctx.setBlendMode(.softLight)
                let tileSize: CGFloat = 128
                // Cheap deterministic "flicker": jump the tile origin every
                // frame instead of regenerating noise (which would cost a
                // fresh image allocation per frame for ~thousands of frames).
                let jx = CGFloat(Int(t * 24) % Int(tileSize))
                let jy = CGFloat(Int(t * 17) % Int(tileSize))
                let rect = CGRect(x: -jx, y: -jy, width: canvas.width + tileSize, height: canvas.height + tileSize)
                ctx.draw(tile, in: rect, byTiling: true)
            }
        case .lightLeak:
            ctx.setBlendMode(.screen)
            let cycle = 6.0
            let phase = (t.truncatingRemainder(dividingBy: cycle)) / cycle
            let cx = canvas.width * CGFloat(-0.3 + 1.6 * phase)
            let cy = canvas.height * 0.22
            let radius = max(canvas.width, canvas.height) * 0.65
            let colors = [
                UIColor(red: 1.0, green: 0.75, blue: 0.45, alpha: amount * 0.55).cgColor,
                UIColor(red: 1.0, green: 0.5, blue: 0.3, alpha: 0).cgColor,
            ]
            if let grad = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colors as CFArray, locations: [0, 1]) {
                ctx.drawRadialGradient(grad, startCenter: CGPoint(x: cx, y: cy), startRadius: 0,
                                        endCenter: CGPoint(x: cx, y: cy), endRadius: radius, options: [])
            }
        }
        ctx.restoreGState()
    }

    // MARK: - Captions
    //
    // Six mechanisms, matching the PC's own six (avg/pipeline/phases/
    // captions.py's `_animated_text`, "not reskins of one another"):
    // pop=scale overshoot, fade=opacity, rise=move+opacity, highlight=
    // cumulative colour fill that stays filled, typewriter=a single
    // instantaneous highlight that MOVES with the voice (not left behind).
    // highlight/typewriter are approximated at WORD granularity (colour
    // per whole word) rather than the PC's sub-word `\kf` character sweep -
    // a deliberate simplification for a per-frame Core Graphics draw, not a
    // silent gap: the visible effect (fills as spoken, in sync) is the same.

    // The look of a caption = layout (text, font, box) + motion (entrance
    // scale/alpha/offset). They are separate so the GPU path can draw the
    // text into a bitmap only when the WORDS' colours change, and animate
    // the entrance on the GPU for free.

    private struct CaptionMotion {
        var scale: CGFloat
        var alpha: CGFloat
        var offsetY: CGFloat
    }

    /// Entrance transform: only the three cue-level styles move/scale the
    /// whole block. highlight/typewriter are fully in place immediately
    /// (matching the PC: no \move/\t(alpha) override on those two) since
    /// their animation lives in per-word colour instead.
    private static func captionMotion(cue: CaptionCue, t: Double, style: CaptionStyle, canvas: CGRect) -> CaptionMotion {
        let age = t - cue.start
        var m = CaptionMotion(scale: 1, alpha: 1, offsetY: 0)
        switch style.animation {
        case .none, .highlight, .typewriter:
            break
        case .pop:
            // 0.65 scale at t=0 -> overshoot to 1.08 by 90ms -> settle to
            // 1.0 by 190ms. Matches the PC's _POP_IN_OVERRIDE curve shape.
            if age < 0.09 {
                let p = max(0, age) / 0.09
                m.scale = 0.65 + (1.08 - 0.65) * CGFloat(pow(p, 0.7))
            } else if age < 0.19 {
                let p = (age - 0.09) / 0.10
                m.scale = 1.08 + (1.0 - 1.08) * CGFloat(p)
            } else {
                m.scale = 1.0
            }
        case .fade:
            m.alpha = age < 0.18 ? CGFloat(max(0, age) / 0.18) : 1
        case .rise:
            let d = min(max(age, 0), 0.22) / 0.22
            m.alpha = CGFloat(d)
            let dist = max(6, 34 * canvas.width / 1920) // PC's _res_scale, long-edge-relative
            m.offsetY = -dist * (1 - CGFloat(d))
        }
        return m
    }

    /// The caption's attributed text and the box it is drawn in (top-down
    /// coordinates, as UIKit text drawing wants).
    private func captionLayout(canvas: CGRect, cue: CaptionCue, t: Double, style: CaptionStyle, quantizeSteps: Int?) -> (str: NSAttributedString, rect: CGRect, textSize: CGSize) {
        let size = style.fontSize * canvas.width / 1080
        let font = themedFont(family: style.fontFamily, size: size, weight: .black, rounded: true)
        let para = NSMutableParagraphStyle()
        para.alignment = .center
        para.lineBreakMode = .byWordWrapping

        let str = attributedString(for: cue, t: t, style: style, font: font, para: para, quantizeSteps: quantizeSteps)
        let maxW = canvas.width * 0.86
        let bounds = str.boundingRect(with: CGSize(width: maxW, height: canvas.height), options: [.usesLineFragmentOrigin], context: nil)
        let y: CGFloat
        switch style.position {
        case .top: y = canvas.height * 0.14
        case .center: y = (canvas.height - bounds.height) / 2
        case .bottom: y = canvas.height * 0.78 - bounds.height
        }
        return (str, CGRect(x: (canvas.width - maxW) / 2, y: y, width: maxW, height: bounds.height + 4),
                CGSize(width: ceil(bounds.width), height: ceil(bounds.height)))
    }

    private func paintCaption(_ ctx: CGContext, canvas: CGRect, str: NSAttributedString, rect: CGRect, textSize: CGSize,
                              overlay: OverlayStyle, motion: CaptionMotion) {
        ctx.saveGState()
        ctx.setAlpha(motion.alpha)
        ctx.translateBy(x: 0, y: canvas.height)
        ctx.scaleBy(x: 1, y: -1)
        ctx.translateBy(x: rect.midX, y: rect.midY - motion.offsetY)
        ctx.scaleBy(x: motion.scale, y: motion.scale)
        ctx.translateBy(x: -rect.midX, y: -rect.midY)
        if overlay.captionBackground != .none {
            // A plate hugging the TEXT (not the full-width layout box), drawn
            // before the shadow is set so the plate itself isn't blurred.
            let padX = textSize.height * 0.16 + 14
            let padY = textSize.height * 0.08 + 6
            let plate = CGRect(x: rect.midX - textSize.width / 2 - padX, y: rect.minY - padY + 2,
                               width: textSize.width + padX * 2, height: textSize.height + padY * 2)
            let radius = overlay.captionBackground == .pill ? plate.height / 2 : 14
            ctx.setFillColor(UIColor.black.withAlphaComponent(CGFloat(min(1, max(0, overlay.captionBackgroundOpacity)))).cgColor)
            ctx.addPath(UIBezierPath(roundedRect: plate, cornerRadius: min(radius, plate.height / 2)).cgPath)
            ctx.fillPath()
        }
        if overlay.captionShadow {
            ctx.setShadow(offset: CGSize(width: 0, height: 4), blur: 12, color: UIColor.black.withAlphaComponent(0.6).cgColor)
        }
        UIGraphicsPushContext(ctx)
        str.draw(with: rect, options: [.usesLineFragmentOrigin], context: nil)
        UIGraphicsPopContext()
        ctx.restoreGState()
    }

    /// CPU path: lay out, animate and paint straight into the frame.
    private func drawCaption(_ ctx: CGContext, canvas: CGRect, cue: CaptionCue, t: Double, style: CaptionStyle) {
        let layout = captionLayout(canvas: canvas, cue: cue, t: t, style: style, quantizeSteps: nil)
        let motion = Self.captionMotion(cue: cue, t: t, style: style, canvas: canvas)
        paintCaption(ctx, canvas: canvas, str: layout.str, rect: layout.rect, textSize: layout.textSize, overlay: style.overlay, motion: motion)
    }

    /// GPU path: paint the caption ONCE at rest (scale 1, opaque, in place)
    /// into a transparent canvas-sized bitmap. The entrance animation is then
    /// a transform + alpha on the GPU.
    private func buildCaptionOverlay(cue: CaptionCue, t: Double, style: CaptionStyle, canvas: CGRect) -> CaptionOverlay? {
        let layout = captionLayout(canvas: canvas, cue: cue, t: t, style: style, quantizeSteps: Self.highlightSteps)
        guard let ctx = makeContext(Int(canvas.width), Int(canvas.height)) else { return nil }
        paintCaption(ctx, canvas: canvas, str: layout.str, rect: layout.rect, textSize: layout.textSize, overlay: style.overlay,
                     motion: CaptionMotion(scale: 1, alpha: 1, offsetY: 0))
        guard let cg = ctx.makeImage() else { return nil }
        return CaptionOverlay(image: CIImage(cgImage: cg), rect: layout.rect)
    }

    /// Changes exactly when `attributedString` would produce different word
    /// colours - so a cached caption bitmap is reused for every frame that
    /// would have looked the same.
    private func captionSignature(cue: CaptionCue, t: Double, style: CaptionStyle) -> Int {
        switch style.animation {
        case .none, .pop, .fade, .rise:
            return 0
        case .highlight, .typewriter:
            guard !cue.words.isEmpty else { return 0 }
            var hasher = Hasher()
            for w in cue.words {
                if style.animation == .highlight {
                    if t >= w.end {
                        hasher.combine(-1)
                    } else if t >= w.start {
                        let p = w.end > w.start ? (t - w.start) / (w.end - w.start) : 1
                        hasher.combine(Int((p * Double(Self.highlightSteps)).rounded()))
                    } else {
                        hasher.combine(-2)
                    }
                } else {
                    hasher.combine((t >= w.start && t < w.end) ? 1 : 0)
                }
            }
            return hasher.finalize()
        }
    }

    /// Bottom-left-origin transform that scales the caption about its own
    /// centre and applies the rise offset. (`rect` is top-down, so its centre
    /// is flipped into the canvas's bottom-left space; a top-down drop of
    /// `-offsetY` is a bottom-left shift of `offsetY`.)
    private static func captionTransform(rect: CGRect, canvas: CGRect, motion: CaptionMotion) -> CGAffineTransform {
        let cx = rect.midX
        let cy = canvas.height - rect.midY
        return CGAffineTransform(translationX: -cx, y: -cy)
            .concatenating(CGAffineTransform(scaleX: motion.scale, y: motion.scale))
            .concatenating(CGAffineTransform(translationX: cx, y: cy + motion.offsetY))
    }

    private func attributedString(for cue: CaptionCue, t: Double, style: CaptionStyle, font: UIFont, para: NSParagraphStyle, quantizeSteps: Int? = nil) -> NSAttributedString {
        let overlay = style.overlay
        let base = Theme.color(hex: overlay.captionTextColorHex).map { UIColor($0) } ?? UIColor.white
        func shown(_ text: String) -> String { overlay.captionUppercase ? text.uppercased() : text }
        var common: [NSAttributedString.Key: Any] = [.font: font, .paragraphStyle: para]
        if overlay.captionOutline > 0 {
            // Negative width = stroke AND fill (positive would hollow the glyphs).
            common[.strokeColor] = UIColor.black
            common[.strokeWidth] = -CGFloat(overlay.captionOutline)
        }
        switch style.animation {
        case .highlight, .typewriter:
            guard !cue.words.isEmpty else {
                return NSAttributedString(string: shown(cue.text), attributes: common.merging([.foregroundColor: base]) { a, _ in a })
            }
            let accent = UIColor(style.accent)
            let out = NSMutableAttributedString()
            for (i, w) in cue.words.enumerated() {
                var color = base
                if style.animation == .highlight {
                    if t >= w.end { color = accent }
                    else if t >= w.start {
                        var p = w.end > w.start ? CGFloat((t - w.start) / (w.end - w.start)) : 1
                        // Snap to a few steps when a cached bitmap is being
                        // built (see `captionSignature`, which must agree).
                        if let q = quantizeSteps { p = (p * CGFloat(q)).rounded() / CGFloat(q) }
                        color = lerp(base, accent, p)
                    }
                } else { // typewriter: a moving snap, never left behind
                    color = (t >= w.start && t < w.end) ? accent : base
                }
                var attrs = common
                attrs[.foregroundColor] = color
                out.append(NSAttributedString(string: shown(w.text), attributes: attrs))
                if i < cue.words.count - 1 { out.append(NSAttributedString(string: " ", attributes: attrs)) }
            }
            return out
        case .none, .pop, .fade, .rise:
            var attrs = common
            attrs[.foregroundColor] = base
            return NSAttributedString(string: shown(cue.text), attributes: attrs)
        }
    }

    private func lerp(_ a: UIColor, _ b: UIColor, _ p: CGFloat) -> UIColor {
        var (ar, ag, ab, aa) = (CGFloat(0), CGFloat(0), CGFloat(0), CGFloat(0))
        var (br, bg, bb, ba) = (CGFloat(0), CGFloat(0), CGFloat(0), CGFloat(0))
        a.getRed(&ar, green: &ag, blue: &ab, alpha: &aa)
        b.getRed(&br, green: &bg, blue: &bb, alpha: &ba)
        let c = max(0, min(1, p))
        return UIColor(red: ar + (br - ar) * c, green: ag + (bg - ag) * c, blue: ab + (bb - ab) * c, alpha: aa + (ba - aa) * c)
    }
}

extension Array {
    subscript(safe i: Int, default d: Element) -> Element {
        indices.contains(i) ? self[i] : d
    }
}
