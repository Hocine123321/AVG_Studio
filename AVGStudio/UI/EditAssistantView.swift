import SwiftUI

/// The editing assistant's chat, a half-height sheet so the preview above it
/// stays visible (and usable) while you ask for changes.
struct EditAssistantView: View {
    let assistant: EditAssistant
    @Environment(\.dismiss) private var dismiss
    @State private var draft = ""

    private static let ideas = [
        "Fix a misheard word in the captions",
        "Add a title card at 5 seconds",
        "Turn the music off for this video",
    ]

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 10) {
                            if assistant.lines.isEmpty { intro }
                            ForEach(assistant.lines) { line in
                                bubble(line).id(line.id)
                            }
                            if assistant.isWorking {
                                ProgressView().padding(.leading, 8)
                            }
                        }
                        .padding(16)
                    }
                    .onChange(of: assistant.lines.count) { _, _ in
                        if let last = assistant.lines.last {
                            withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                        }
                    }
                }
                inputBar
            }
            .navigationTitle("Editing assistant")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Undo") { assistant.undo() }
                        .disabled(!assistant.canUndo || assistant.isWorking)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
            .tint(Theme.accent)
        }
        .presentationDetents([.fraction(0.5), .large])
        .presentationBackgroundInteraction(.enabled(upThrough: .fraction(0.5)))
        .glassSheetBackground()
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Ask for a change to this video. I can fix captions, add or remove title cards, and choose its music and sound effects. Nothing is rendered until you press Render.")
                .font(.callout)
                .foregroundStyle(.secondary)
            ForEach(Self.ideas, id: \.self) { idea in
                Button(idea) { draft = idea }.buttonStyle(.glass)
            }
        }
    }

    @ViewBuilder private func bubble(_ line: EditAssistant.Line) -> some View {
        switch line.kind {
        case .user:
            Text(line.text)
                .padding(.horizontal, 12).padding(.vertical, 8)
                .background(Theme.accent.opacity(0.25), in: .rect(cornerRadius: 16))
                .frame(maxWidth: .infinity, alignment: .trailing)
        case .assistant:
            Text(line.text)
                .padding(.horizontal, 12).padding(.vertical, 8)
                .background(.thinMaterial, in: .rect(cornerRadius: 16))
                .frame(maxWidth: .infinity, alignment: .leading)
        case .activity:
            Text(line.text).font(.caption).foregroundStyle(.secondary)
        case .error:
            Text(line.text)
                .font(.callout)
                .foregroundStyle(.red)
                .padding(.horizontal, 12).padding(.vertical, 8)
                .background(Color.red.opacity(0.12), in: .rect(cornerRadius: 16))
        }
    }

    private var inputBar: some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField("Ask for an edit", text: $draft, axis: .vertical)
                .lineLimit(1...4)
                .padding(.horizontal, 12).padding(.vertical, 10)
                .background(.thinMaterial, in: .rect(cornerRadius: 18))
            if assistant.isWorking {
                Button { assistant.stop() } label: {
                    Image(systemName: "stop.circle.fill").font(.title)
                }
                .accessibilityLabel("Stop")
            } else {
                Button { send() } label: {
                    Image(systemName: "arrow.up.circle.fill").font(.title)
                }
                .disabled(draft.trimmed.isEmpty)
                .accessibilityLabel("Send")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private func send() {
        let text = draft
        draft = ""
        assistant.send(text)
    }
}
