import Foundation
import AVFoundation
import UIKit
import CoreVideo

struct CaptionStyle {
    var enabled: Bool
    var fontSize: CGFloat
    var position: CaptionPosition
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
    var output: URL
}

/// Replaces the PC's ffmpeg engine: frames are drawn with Core Graphics
/// and encoded by the iPhone's hardware HEVC encoder, then muxed with the narration.
final class VideoRenderer {
    private let crossfade: Double = 0.35

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

        if input.captions.enabled, let cue = input.cues.first(where: { t >= $0.start && t < $0.end }) {
            drawCaption(ctx, canvas: canvas, cue: cue, t: t, style: input.captions)
        }
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

    private func drawCaption(_ ctx: CGContext, canvas: CGRect, cue: CaptionCue, t: Double, style: CaptionStyle) {
        let size = style.fontSize * canvas.width / 1080
        let base = UIFont.systemFont(ofSize: size, weight: .black)
        let font = base.fontDescriptor.withDesign(.rounded).map { UIFont(descriptor: $0, size: size) } ?? base
        let para = NSMutableParagraphStyle()
        para.alignment = .center
        para.lineBreakMode = .byWordWrapping
        let text = cue.text.uppercased()
        let attrs: [NSAttributedString.Key: Any] = [
            .font: font, .foregroundColor: UIColor.white, .paragraphStyle: para,
            .strokeColor: UIColor.black, .strokeWidth: -5.0,
        ]
        let str = NSAttributedString(string: text, attributes: attrs)
        let maxW = canvas.width * 0.86
        let bounds = str.boundingRect(with: CGSize(width: maxW, height: canvas.height), options: [.usesLineFragmentOrigin], context: nil)
        let y: CGFloat
        switch style.position {
        case .top: y = canvas.height * 0.14
        case .center: y = (canvas.height - bounds.height) / 2
        case .bottom: y = canvas.height * 0.78 - bounds.height
        }
        // "Pop" in: quick scale from 85% during the first 120 ms.
        let age = t - cue.start
        let pop = CGFloat(age < 0.12 ? 0.85 + 0.15 * (age / 0.12) : 1)
        let rect = CGRect(x: (canvas.width - maxW) / 2, y: y, width: maxW, height: bounds.height + 4)

        ctx.saveGState()
        ctx.translateBy(x: 0, y: canvas.height)
        ctx.scaleBy(x: 1, y: -1)
        ctx.translateBy(x: rect.midX, y: rect.midY)
        ctx.scaleBy(x: pop, y: pop)
        ctx.translateBy(x: -rect.midX, y: -rect.midY)
        ctx.setShadow(offset: CGSize(width: 0, height: 4), blur: 12, color: UIColor.black.withAlphaComponent(0.6).cgColor)
        UIGraphicsPushContext(ctx)
        str.draw(with: rect, options: [.usesLineFragmentOrigin], context: nil)
        UIGraphicsPopContext()
        ctx.restoreGState()
    }
}

extension Array {
    subscript(safe i: Int, default d: Element) -> Element {
        indices.contains(i) ? self[i] : d
    }
}
