import Foundation
import AVFoundation
import UIKit
import CoreVideo
import SwiftUI

struct CaptionStyle {
    var enabled: Bool
    var fontSize: CGFloat
    var position: CaptionPosition
    var animation: CaptionAnimationStyle = .fade
    var accent: Color = Theme.accent
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
    var captions: CaptionStyle
    var filter: FilterSpec = FilterSpec(style: .none, intensity: 0)
    var cards: [TakeoverCard] = []
    var output: URL
}

/// Replaces the PC's ffmpeg engine: frames are drawn with Core Graphics
/// and encoded by the iPhone's hardware HEVC encoder, then muxed with the narration.
final class VideoRenderer {
    private let crossfade: Double = 0.35
    private lazy var grainTile: CGImage? = Self.makeGrainTile()

    func render(_ input: RenderInput, progress: @escaping (Double) -> Void) async throws {
        let fm = FileManager.default
        let folder = input.output.deletingLastPathComponent()
        let tmp = folder.appendingPathComponent("video_only.mov")
        try? fm.removeItem(at: tmp)
        try? fm.removeItem(at: input.output)

        let w = Int(input.size.width), h = Int(input.size.height)
        let writer = try AVAssetWriter(outputURL: tmp, fileType: .mov)
        let vIn = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.hevc,
            AVVideoWidthKey: w,
            AVVideoHeightKey: h,
        ])
        vIn.expectsMediaDataInRealTime = false
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: vIn, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: w,
            kCVPixelBufferHeightKey as String: h,
        ])
        writer.add(vIn)
        guard writer.startWriting() else { throw writer.error ?? AVGError.message("Couldn't start the video encoder.") }
        writer.startSession(atSourceTime: .zero)

        let images: [CGImage?] = input.images.map { url in
            guard let url, let img = UIImage(contentsOfFile: url.path) else { return nil }
            return img.cgImage
        }
        var starts: [Double] = []
        var acc = 0.0
        for d in input.sceneDurations { starts.append(acc); acc += d }
        let total = max(acc, 0.5)
        let frames = Int(total * Double(input.fps))

        for f in 0..<frames {
            try Task.checkCancellation()
            while !vIn.isReadyForMoreMediaData { try await Task.sleep(nanoseconds: 2_000_000) }
            guard let pool = adaptor.pixelBufferPool else { throw AVGError.message("Encoder pool unavailable.") }
            var pbOut: CVPixelBuffer?
            CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pbOut)
            guard let pb = pbOut else { throw AVGError.message("Out of memory while rendering.") }
            let t = Double(f) / Double(input.fps)
            autoreleasepool {
                draw(into: pb, t: t, input: input, images: images, starts: starts)
            }
            if !adaptor.append(pb, withPresentationTime: CMTime(value: Int64(f), timescale: Int32(input.fps))) {
                throw writer.error ?? AVGError.message("The encoder rejected a frame.")
            }
            if f % 15 == 0 { progress(Double(f) / Double(max(frames, 1)) * 0.85) }
        }
        vIn.markAsFinished()
        await writer.finishWriting()
        if writer.status != .completed { throw writer.error ?? AVGError.message("Video encoding failed.") }

        // Mux narration.
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
        guard let export = AVAssetExportSession(asset: comp, presetName: AVAssetExportPresetHEVCHighestQuality) else {
            throw AVGError.message("Couldn't create the exporter.")
        }
        progress(0.9)
        try await export.export(to: input.output, as: .mp4)
        try? fm.removeItem(at: tmp)
        progress(1)
    }

    // MARK: - Frame drawing

    private func draw(into pb: CVPixelBuffer, t: Double, input: RenderInput, images: [CGImage?], starts: [Double]) {
        CVPixelBufferLockBaseAddress(pb, [])
        defer { CVPixelBufferUnlockBaseAddress(pb, []) }
        let w = CVPixelBufferGetWidth(pb), h = CVPixelBufferGetHeight(pb)
        guard let ctx = CGContext(data: CVPixelBufferGetBaseAddress(pb), width: w, height: h, bitsPerComponent: 8,
                                  bytesPerRow: CVPixelBufferGetBytesPerRow(pb), space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
        else { return }
        let canvas = CGRect(x: 0, y: 0, width: w, height: h)
        ctx.setFillColor(UIColor.black.cgColor)
        ctx.fill(canvas)
        ctx.interpolationQuality = .high

        // A takeover card OWNS its whole scene window - no scene image,
        // no Ken Burns, no crossfade, no captions underneath it, cutting
        // in/out instantly. Mirrors overlay_design.py's own "every card
        // renders full-bleed/opaque... not floating over footage".
        if let card = input.cards.first(where: { t >= $0.start && t < $0.end }) {
            drawCard(ctx, canvas: canvas, card: card, accent: UIColor(input.captions.accent))
            applyFilter(ctx, canvas: canvas, t: t, filter: input.filter)
            return
        }

        var idx = 0
        for (i, s) in starts.enumerated() where t >= s { idx = i }
        let dur = input.sceneDurations.indices.contains(idx) ? input.sceneDurations[idx] : 1
        let local = min(max((t - starts[safe: idx, default: 0]) / max(dur, 0.01), 0), 1)

        drawScene(ctx, canvas: canvas, image: images[safe: idx, default: nil], sceneIndex: idx, p: local, input: input, alpha: 1)
        // Crossfade into the next scene.
        let remaining = (starts[safe: idx, default: 0] + dur) - t
        if remaining < crossfade, idx + 1 < images.count {
            let a = 1 - remaining / crossfade
            drawScene(ctx, canvas: canvas, image: images[idx + 1], sceneIndex: idx + 1, p: 0, input: input, alpha: a)
        }

        // Filter overlay sits under captions, same as the PC's own pass
        // ordering (its `compose_filter_overlays` runs before the caption
        // burn pass), so grain/light leak never fights caption legibility.
        applyFilter(ctx, canvas: canvas, t: t, filter: input.filter)

        if input.captions.enabled, let cue = input.cues.first(where: { t >= $0.start && t < $0.end }) {
            drawCaption(ctx, canvas: canvas, cue: cue, t: t, style: input.captions)
        }
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

    private func drawCard(_ ctx: CGContext, canvas: CGRect, card: TakeoverCard, accent: UIColor) {
        ctx.saveGState()
        // Matches overlay_design.py's DEFAULT_BRAND["bg"] (#0B0F1A) - the
        // card owns the frame, so this replaces the scene entirely.
        ctx.setFillColor(UIColor(red: 0x0B / 255.0, green: 0x0F / 255.0, blue: 0x1A / 255.0, alpha: 1).cgColor)
        ctx.fill(canvas)

        let boxW = canvas.width * 0.84
        let boxH = canvas.height * 0.72
        let shortEdge = min(canvas.width, canvas.height)
        let floorSize = max(14, shortEdge * 0.035)
        let maxLines = 2 // "Focused" cards only - the PC's AI-driven "Explainer" cards aren't ported (see TakeoverCard's doc comment).
        let para = NSMutableParagraphStyle()
        para.alignment = .center
        para.lineBreakMode = .byWordWrapping

        var size = shortEdge * 0.15
        var font = UIFont.systemFont(ofSize: size, weight: .heavy)
        while size > floorSize {
            font = UIFont.systemFont(ofSize: size, weight: .heavy)
            let probe = NSAttributedString(string: card.text, attributes: [.font: font, .paragraphStyle: para])
            let bounds = probe.boundingRect(with: CGSize(width: boxW, height: .greatestFiniteMagnitude), options: [.usesLineFragmentOrigin], context: nil)
            let lines = max(1, Int((bounds.height / font.lineHeight).rounded(.up)))
            if bounds.height <= boxH, lines <= maxLines { break }
            size -= max(2, size * 0.06)
        }

        let str = NSAttributedString(string: card.text, attributes: [
            .font: font, .paragraphStyle: para, .foregroundColor: UIColor(white: 0.95, alpha: 1),
        ])
        let bounds = str.boundingRect(with: CGSize(width: boxW, height: boxH), options: [.usesLineFragmentOrigin], context: nil)
        let rect = CGRect(x: (canvas.width - boxW) / 2, y: (canvas.height - bounds.height) / 2, width: boxW, height: bounds.height)

        ctx.translateBy(x: 0, y: canvas.height)
        ctx.scaleBy(x: 1, y: -1)
        UIGraphicsPushContext(ctx)
        str.draw(with: rect, options: [.usesLineFragmentOrigin], context: nil)
        UIGraphicsPopContext()

        // Accent rule beneath the text block - overlay_design.py's own
        // rounded "rule" divider under a card's headline.
        let ruleW = min(canvas.width * 0.22, shortEdge * 0.18)
        let ruleH = max(3, shortEdge * 0.006)
        let ruleRect = CGRect(x: (canvas.width - ruleW) / 2, y: rect.maxY + shortEdge * 0.03, width: ruleW, height: ruleH)
        ctx.setFillColor(accent.cgColor)
        ctx.addPath(UIBezierPath(roundedRect: ruleRect, cornerRadius: ruleH / 2).cgPath)
        ctx.fillPath()

        ctx.restoreGState()
    }

    private func drawScene(_ ctx: CGContext, canvas: CGRect, image: CGImage?, sceneIndex: Int, p: Double, input: RenderInput, alpha: Double) {
        guard let image else { return }
        let iw = CGFloat(image.width), ih = CGFloat(image.height)
        let fill = max(canvas.width / iw, canvas.height / ih)
        var scale = fill
        var dx: CGFloat = 0, dy: CGFloat = 0
        if input.kenBurns {
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
        let dw = iw * scale, dh = ih * scale
        let rect = CGRect(x: (canvas.width - dw) / 2 + dx, y: (canvas.height - dh) / 2 + dy, width: dw, height: dh)
        ctx.saveGState()
        ctx.setAlpha(CGFloat(alpha))
        ctx.draw(image, in: rect)
        ctx.restoreGState()
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

    private func drawCaption(_ ctx: CGContext, canvas: CGRect, cue: CaptionCue, t: Double, style: CaptionStyle) {
        let size = style.fontSize * canvas.width / 1080
        let base = UIFont.systemFont(ofSize: size, weight: .black)
        let font = base.fontDescriptor.withDesign(.rounded).map { UIFont(descriptor: $0, size: size) } ?? base
        let para = NSMutableParagraphStyle()
        para.alignment = .center
        para.lineBreakMode = .byWordWrapping

        let str = attributedString(for: cue, t: t, style: style, font: font, para: para)
        let maxW = canvas.width * 0.86
        let bounds = str.boundingRect(with: CGSize(width: maxW, height: canvas.height), options: [.usesLineFragmentOrigin], context: nil)
        let y: CGFloat
        switch style.position {
        case .top: y = canvas.height * 0.14
        case .center: y = (canvas.height - bounds.height) / 2
        case .bottom: y = canvas.height * 0.78 - bounds.height
        }
        let rect = CGRect(x: (canvas.width - maxW) / 2, y: y, width: maxW, height: bounds.height + 4)

        // Entrance transform: only the three cue-level styles move/scale the
        // whole block. highlight/typewriter are fully in place immediately
        // (matching the PC: no \move/\t(alpha) override on those two) since
        // their animation lives in per-word colour instead.
        let age = t - cue.start
        var scale: CGFloat = 1
        var alpha: CGFloat = 1
        var offsetY: CGFloat = 0
        switch style.animation {
        case .none, .highlight, .typewriter:
            break
        case .pop:
            // 0.65 scale at t=0 -> overshoot to 1.08 by 90ms -> settle to
            // 1.0 by 190ms. Matches the PC's _POP_IN_OVERRIDE curve shape.
            if age < 0.09 {
                let p = max(0, age) / 0.09
                scale = 0.65 + (1.08 - 0.65) * CGFloat(pow(p, 0.7))
            } else if age < 0.19 {
                let p = (age - 0.09) / 0.10
                scale = 1.08 + (1.0 - 1.08) * CGFloat(p)
            } else {
                scale = 1.0
            }
        case .fade:
            alpha = age < 0.18 ? CGFloat(max(0, age) / 0.18) : 1
        case .rise:
            let d = min(max(age, 0), 0.22) / 0.22
            alpha = CGFloat(d)
            let dist = max(6, 34 * canvas.width / 1920) // PC's _res_scale, long-edge-relative
            offsetY = -dist * (1 - CGFloat(d))
        }

        ctx.saveGState()
        ctx.setAlpha(alpha)
        ctx.translateBy(x: 0, y: canvas.height)
        ctx.scaleBy(x: 1, y: -1)
        ctx.translateBy(x: rect.midX, y: rect.midY - offsetY)
        ctx.scaleBy(x: scale, y: scale)
        ctx.translateBy(x: -rect.midX, y: -rect.midY)
        ctx.setShadow(offset: CGSize(width: 0, height: 4), blur: 12, color: UIColor.black.withAlphaComponent(0.6).cgColor)
        UIGraphicsPushContext(ctx)
        str.draw(with: rect, options: [.usesLineFragmentOrigin], context: nil)
        UIGraphicsPopContext()
        ctx.restoreGState()
    }

    private func attributedString(for cue: CaptionCue, t: Double, style: CaptionStyle, font: UIFont, para: NSParagraphStyle) -> NSAttributedString {
        let strokeAttrs: [NSAttributedString.Key: Any] = [
            .font: font, .paragraphStyle: para, .strokeColor: UIColor.black, .strokeWidth: -5.0,
        ]
        switch style.animation {
        case .highlight, .typewriter:
            guard !cue.words.isEmpty else {
                return NSAttributedString(string: cue.text.uppercased(), attributes: strokeAttrs.merging([.foregroundColor: UIColor.white]) { a, _ in a })
            }
            let accent = UIColor(style.accent)
            let out = NSMutableAttributedString()
            for (i, w) in cue.words.enumerated() {
                var color = UIColor.white
                if style.animation == .highlight {
                    if t >= w.end { color = accent }
                    else if t >= w.start {
                        let p = w.end > w.start ? CGFloat((t - w.start) / (w.end - w.start)) : 1
                        color = lerp(UIColor.white, accent, p)
                    }
                } else { // typewriter: a moving snap, never left behind
                    color = (t >= w.start && t < w.end) ? accent : .white
                }
                var attrs = strokeAttrs
                attrs[.foregroundColor] = color
                out.append(NSAttributedString(string: w.text.uppercased(), attributes: attrs))
                if i < cue.words.count - 1 { out.append(NSAttributedString(string: " ", attributes: attrs)) }
            }
            return out
        case .none, .pop, .fade, .rise:
            var attrs = strokeAttrs
            attrs[.foregroundColor] = UIColor.white
            return NSAttributedString(string: cue.text.uppercased(), attributes: attrs)
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
