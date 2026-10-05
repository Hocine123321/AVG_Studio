import SwiftUI

/// Toolbar entry on Library, Studio and Create. Glass but not prominent, so
/// Create's single filled action stays the only one on its screen.
struct AgentToolbarButton: View {
    @Environment(Store.self) private var store

    var body: some View {
        Button("Assistant", systemImage: "sparkle") {
            Haptics.light()
            store.agent.isOpen = true
        }
        .accessibilityLabel("Open the assistant")
    }
}

/// The chat sheet. All state lives in `store.agent`; this view only draws it
/// and forwards taps. The one filled (`.glassProminent`) button is Approve.
struct AgentView: View {
    @Environment(Store.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var draft = ""
    @FocusState private var focused: Bool

    private var agent: Agent { store.agent }
    private var provider: AgentProviderKind { store.settings.agentProvider }
    private var keyMissing: Bool { !Keychain.has(provider == .groq ? .groq : .gemini) }
    private var canSend: Bool { !draft.trimmed.isEmpty && !agent.isWorking }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if keyMissing { missingKey } else { transcript }
                if let approval = agent.pendingApproval { approvalCard(approval) }
                if agent.approvedSpendUSD > 0 { spendCaption }
                if !keyMissing { composer }
            }
            .navigationTitle("Assistant")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("New chat", systemImage: "square.and.pencil") {
                        Haptics.light()
                        agent.reset()
                    }
                    .disabled(agent.lines.isEmpty && !agent.isWorking)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { agent.isOpen = false }
                }
            }
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .glassSheetBackground(scroll: false)
    }

    // MARK: Transcript

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if agent.lines.isEmpty { intro }
                    ForEach(agent.lines) { line in row(line).id(line.id) }
                    if agent.isWorking && agent.pendingApproval == nil { typing }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 16)
                .padding(.top, 8)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: agent.lines.count) { _, _ in
                withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) { proxy.scrollTo("bottom") }
            }
            .onChange(of: agent.pendingApproval?.id) { _, _ in proxy.scrollTo("bottom") }
        }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Ask me to make a video, check progress, redo a step or stop one.")
                .font(.subheadline).foregroundStyle(Theme.ink)
            Text("Your messages go to \(provider.label). Anything that starts a video asks you first.")
                .font(.footnote).foregroundStyle(Theme.ink2)
        }
        .padding(.vertical, 12)
    }

    @ViewBuilder
    private func row(_ line: Agent.Line) -> some View {
        switch line.kind {
        case .user:
            HStack {
                Spacer(minLength: 48)
                Text(line.text)
                    .font(.subheadline).foregroundStyle(Theme.ink)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(Theme.fill, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            }
        case .assistant:
            Text(line.text)
                .font(.subheadline).foregroundStyle(Theme.ink)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .activity:
            Label(line.text, systemImage: "ellipsis")
                .font(.caption).foregroundStyle(Theme.ink2)
        case .error:
            Label(line.text, systemImage: "exclamationmark.triangle.fill")
                .font(.footnote).foregroundStyle(Theme.bad)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .announcement:
            Label(line.text, systemImage: "bell.fill")
                .font(.footnote.weight(.semibold)).foregroundStyle(Theme.ink)
                .glassCard(16, padding: 10)
        }
    }

    private var typing: some View {
        Image(systemName: "ellipsis")
            .font(.title3).foregroundStyle(Theme.ink2)
            .symbolEffect(.variableColor.iterative, isActive: !reduceMotion)
            .accessibilityLabel("The assistant is working")
    }

    // MARK: No key

    private var missingKey: some View {
        VStack(spacing: 12) {
            Image(systemName: "key.fill").font(.largeTitle).foregroundStyle(Theme.ink2)
            Text("Add your \(provider.label) key to use the assistant.")
                .font(.headline).multilineTextAlignment(.center)
            Text("Your messages go to \(provider.label). Keys stay in this phone's Keychain.")
                .font(.footnote).foregroundStyle(Theme.ink2).multilineTextAlignment(.center)
            Button("Open API keys", systemImage: "key") {
                agent.isOpen = false
                store.tab = .settings
            }
            .buttonStyle(.glass)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: Approval

    private func approvalCard(_ a: AgentApproval) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(a.title).font(.headline).foregroundStyle(Theme.ink)
            Text(a.detail).font(.subheadline).foregroundStyle(Theme.ink2)
            Label("Estimated cost: \(a.estimate)", systemImage: "dollarsign.circle")
                .font(.footnote).foregroundStyle(Theme.ink)
            HStack(spacing: 12) {
                Button("Decline") { Haptics.light(); agent.decline() }
                    .buttonStyle(.glass)
                Button("Approve") { Haptics.light(); agent.approve() }
                    .buttonStyle(.glassProminent)
            }
        }
        .glassCard(24, padding: 16)
        .padding(.horizontal, 16)
        .padding(.bottom, 8)
    }

    private var spendCaption: some View {
        let cap = AgentSettingsRules.spendCap(store.settings.agentChatSpendCapUSD)
        return Text("Approved this chat: \(Agent.dollars(agent.approvedSpendUSD)) of \(Agent.dollars(cap))")
            .font(.caption2).foregroundStyle(Theme.ink2)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 18)
            .padding(.bottom, 4)
    }

    // MARK: Composer

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Ask the assistant…", text: $draft, axis: .vertical)
                .lineLimit(1...4)
                .focused($focused)
                .padding(.horizontal, 14).padding(.vertical, 10)
                .glass(RoundedRectangle(cornerRadius: 20, style: .continuous))
            if agent.isWorking {
                Button { agent.stop() } label: { Image(systemName: "stop.fill") }
                    .buttonStyle(.glass)
                    .accessibilityLabel("Stop")
            } else {
                Button { send() } label: { Image(systemName: "arrow.up") }
                    .buttonStyle(.glass)
                    .disabled(!canSend)
                    .accessibilityLabel("Send")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    private func send() {
        let text = draft
        draft = ""
        Haptics.light()
        agent.send(text)
    }
}
