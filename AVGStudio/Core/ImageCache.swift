import SwiftUI
import UIKit
import ImageIO

/// Small, decoded-once, off-main-thread image loading for thumbnails.
///
/// Why this exists: `ProjectCard`, Studio's scene list and the reusable-image
/// pool all used to call `UIImage(contentsOfFile:)` straight inside `body`.
/// That decodes a full-size JPEG synchronously on the main thread, and
/// because every card also reads `store.jobs` (progress ticks many times a
/// second during a render) every visible card re-decoded its image on every
/// tick. Here each thumbnail is downsampled once by ImageIO, off the main
/// actor, and kept in an `NSCache` that the system can purge under pressure.
enum ImageCache {
    private static let cache: NSCache<NSString, UIImage> = {
        let c = NSCache<NSString, UIImage>()
        c.countLimit = 300
        c.totalCostLimit = 80 * 1024 * 1024
        return c
    }()

    /// Keyed by path, target size AND modification date, so redoing Visuals
    /// (which overwrites `scene_01.jpg` in place) shows the new picture
    /// instead of a stale cached one.
    private static func key(_ url: URL, _ maxPixel: Int) -> NSString {
        let stamp = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?
            .contentModificationDate?.timeIntervalSinceReferenceDate ?? 0
        return "\(url.path)|\(maxPixel)|\(stamp)" as NSString
    }

    static func load(_ url: URL, maxPixel: Int) async -> UIImage? {
        let k = key(url, maxPixel)
        if let hit = cache.object(forKey: k) { return hit }
        let image = await Task.detached(priority: .utility) {
            ImageCache.downsample(url, maxPixel: maxPixel)
        }.value
        if let image {
            let cost = Int(image.size.width * image.scale * image.size.height * image.scale) * 4
            cache.setObject(image, forKey: k, cost: cost)
        }
        return image
    }

    fileprivate static func downsample(_ url: URL, maxPixel: Int) -> UIImage? {
        let sourceOptions: [CFString: Any] = [kCGImageSourceShouldCache: false]
        guard let source = CGImageSourceCreateWithURL(url as CFURL, sourceOptions as CFDictionary) else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixel,
        ]
        guard let cg = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else { return nil }
        return UIImage(cgImage: cg)
    }
}

/// A picture that loads itself asynchronously and fills its frame, drawing
/// nothing until it arrives - put it over a placeholder in a `ZStack`.
struct CachedThumb: View {
    let url: URL?
    /// Longest edge, in PIXELS, to decode to. 2x the largest on-screen size
    /// is plenty; the default suits a library card.
    var maxPixel: Int = 720
    @State private var image: UIImage?

    var body: some View {
        ZStack {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
                    .transition(.opacity)
            }
        }
        .task(id: url) {
            guard let url else { image = nil; return }
            let loaded = await ImageCache.load(url, maxPixel: maxPixel)
            withAnimation(.easeOut(duration: 0.2)) { image = loaded }
        }
    }
}
