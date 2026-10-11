import Foundation

/// Models write Markdown with BLOCKS - "* item" bullets, "# Heading" - that
/// SwiftUI's inline-only parser would show as literal asterisks and hashes.
/// This turns those lines into plain text the inline parser then styles.
enum AgentMarkdown {
    static func normalize(_ text: String) -> String {
        text.components(separatedBy: "\n").map { line -> String in
            let body = line.drop(while: { $0 == " " || $0 == "\t" })
            let indent = String(line.prefix(line.count - body.count))
            // "* item", "- item", "+ item" -> a real bullet. A line that merely
            // STARTS with bold ("**x**") has no space after the star and is left alone.
            for marker in ["* ", "- ", "+ ", "• "] where body.hasPrefix(marker) {
                return indent + "•  " + body.dropFirst(marker.count)
            }
            // "## Title" -> bold.
            if body.hasPrefix("#") {
                let title = body.drop(while: { $0 == "#" })
                if title.first == " ", !title.dropFirst().isEmpty { return indent + "**" + title.dropFirst() + "**" }
            }
            return line
        }.joined(separator: "\n")
    }

    /// Normalised, then parsed for bold, italic, code and links.
    static func attributed(_ text: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(
            allowsExtendedAttributes: false,
            interpretedSyntax: .inlineOnlyPreservingWhitespace,
            failurePolicy: .returnPartiallyParsedIfPossible)
        let source = normalize(text)
        return (try? AttributedString(markdown: source, options: options)) ?? AttributedString(source)
    }
}
