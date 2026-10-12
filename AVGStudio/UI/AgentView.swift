import SwiftUI

/// Toolbar entry on Library, Studio and Create. A plain toolbar button: iOS 26
/// draws it as Liquid Glass by itself, and it is not a filled action, so
/// Create's single `.glassProminent` button stays the only one on its screen.
struct AgentToolbarButton: View {
    @Environment(Store.self) private var store

    var body: some View {
        Button {
            Haptics.light()
            store.agent.isOpen = true
        } label: {
            Label("Assistant", systemImage: "sparkle").labelStyle(.iconOnly)
        }
        .accessibilityLabel("Open the assistant")
    }
}

/// The assistant's chat sheet. All state lives in `store.agent` (and the
/// store); this view draws it and forwards taps.
///
/// Everything that needs the person's attention sits in one glass stack at
/// the bottom, above the composer: a paused video's recovery choices, an
/// approval, live progress for videos the assistant started. So an error is
/// never somewhere else - it is right here with buttons that fix it. The one
/// filled (`.glassProminent`) button on the screen is Approve.
struct AgentView: View {
    @Environment(Store.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var draft = ""
    @State private var detent: PresentationDetent = .medium
    @State private var attentionHeight: CGFloat = 0
    @FocusState private var focused: Bool

    private var agent: Agent { store.agent }
    private var provider: AgentProviderKind { store.settings.agentProvider }
    private var keyMissing: Bool { !Keychain.has(provider.apiKey) }
    private var canSend: Bool { !draft.trimmed.isEmpty && !agent.isWorking }

    var body: some View {
        NavigationStack {
            Group {
                if keyMissing { missingKey } else { transcript }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) { bottomStack }
            .navigationTitle("Assistant")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        Haptics.light()
                        agent.reset()
                    } label: {
                        Label("New chat", systemImage: "square.and.pencil").labelStyle(.iconOnly)
                    }
                    .disabled(agent.lines.isEmpty && !agent.isWorking)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done", role: .close) { agent.isOpen = false }
                }
            }
        }
        // No custom sheet background: at the medium height iOS 26 draws the
        // sheet as inset Liquid Glass, and the app behind it stays usable.
        .presentationDetents([.medium, .large], selection: $detent)
        .presentationBackgroundInteraction(.enabled(upThrough: .medium))
        .presentationDragIndicator(.visible)
        // An approval or a paused video needs room to read and answer: open up.
        .onChange(of: agent.pendingApproval?.id) { _, id in if id != nil { detent = .large } }
        .onChange(of: store.recoveryPlan?.id) { _, id in if id != nil { detent = .large } }
    }

    // MARK: Transcript

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if agent.lines.isEmpty { intro }
                    ForEach(agent.lines) { line in row(line).id(line.id) }
                    if agent.isWorking && agent.pendingApproval == nil { typing }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 16)
                .padding(.top, 8)
            }
            .scrollDismissesKeyboard(.interactively)
            .scrollEdgeEffectStyle(.soft, for: .bottom)
            .onChange(of: agent.lines.count) { _, _ in scrollToEnd(proxy) }
            .onChange(of: agent.pendingApproval?.id) { _, _ in scrollToEnd(proxy) }
            .onChange(of: store.recoveryPlan?.id) { _, _ in scrollToEnd(proxy) }
            .onAppear { proxy.scrollTo("bottom") }
        }
    }

    private func scrollToEnd(_ proxy: ScrollViewProxy) {
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) { proxy.scrollTo("bottom") }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                Text("What would you like to do?")
                    .font(.headline).foregroundStyle(.primary)
                Text("Your messages go to \(provider.label). Anything that starts a video asks you first.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 8) {
                suggestion("What's in my library?", symbol: "square.grid.2x2") { agent.send("What's in my library?") }
                suggestion("How is my video going?", symbol: "gauge.with.dots.needle.50percent") { agent.send("How is my latest video going?") }
                suggestion("Make a video about…", symbol: "wand.and.stars") {
                    draft = "Make a video about "
                    focused = true
                }
            }
        }
        .padding(.vertical, 12)
    }

    private func suggestion(_ title: String, symbol: String, action: @escaping () -> Void) -> some View {
        Button {
            Haptics.light()
            action()
        } label: {
            Label(title, systemImage: symbol)
                .font(.subheadline.weight(.medium))
        }
        .buttonStyle(.glass)
    }

    @ViewBuilder
    private func row(_ line: Agent.Line) -> some View {
        switch line.kind {
        case .user:
            HStack {
                Spacer(minLength: 48)
                Text(line.text)
                    .font(.subheadline).foregroundStyle(.primary)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(Theme.accent.opacity(0.2), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                    .textSelection(.enabled)
            }
        case .assistant:
            Text(Self.markdown(line.text))
                .font(.subheadline).foregroundStyle(.primary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
                .contextMenu { Button("Copy", systemImage: "doc.on.doc") { UIPasteboard.general.string = line.text } }
        case .activity:
            Label(line.text, systemImage: "ellipsis")
                .font(.caption).foregroundStyle(.secondary)
        case .error:
            errorRow(line)
        case .announcement:
            Label(line.text, systemImage: "bell.fill")
                .font(.footnote.weight(.semibold)).foregroundStyle(.primary)
                .padding(.horizontal, 14).padding(.vertical, 10)
                .liquidGlass(RoundedRectangle(cornerRadius: 18, style: .continuous), tint: Theme.ok.opacity(0.25))
        }
    }

    /// An assistant failure in plain words, with buttons that fix it. Only the
    /// newest error is actionable; an older one is just history.
    private func errorRow(_ line: Agent.Line) -> some View {
        let actionable = line.id == agent.lines.last?.id
        return VStack(alignment: .leading, spacing: 8) {
            Label(line.title ?? "Something went wrong", systemImage: "exclamationmark.triangle.fill")
                .font(.footnote.weight(.semibold)).foregroundStyle(Theme.bad)
            Text(line.text)
                .font(.footnote).foregroundStyle(.primary)
            if actionable && !line.fixes.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(line.fixes, id: \.self) { fix in
                        Button {
                            Haptics.light()
                            apply(fix)
                        } label: {
                            Label(fix.label, systemImage: fix.symbol)
                                .font(.footnote.weight(.semibold))
                        }
                        .buttonStyle(.glass)
                        .controlSize(.small)
                    }
                }
                .padding(.top, 2)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.bad.opacity(0.12), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    /// What each fix button does. They navigate or change one setting; none
    /// of them starts paid work by itself (a retry goes through the same
    /// approval as any other message).
    private func apply(_ fix: AgentFix) {
        switch fix {
        case .retry:
            agent.retryLast()
        case .switchProvider(let kind):
            // For this chat only - the service chosen in Settings stays as it is.
            agent.useForThisChat(kind)
            agent.retryLast()
        case .openKeys:
            agent.isOpen = false
            store.openSettings(.category(.keys, focus: .apiKeys))
        case .openAssistantSettings:
            agent.isOpen = false
            store.openSettings(.category(.assistant, focus: .assistantModel))
        }
    }

    private var typing: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text("Working…").font(.caption).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }

    /// Bold, italic, code, links and bullets the way models write them.
    static func markdown(_ text: String) -> AttributedString { AgentMarkdown.attributed(text) }

    // MARK: No key

    private var missingKey: some View {
        VStack(spacing: 12) {
            Image(systemName: "key.fill").font(.largeTitle).foregroundStyle(.secondary)
            Text("Add your \(provider.label) key to use the assistant.")
                .font(.headline).multilineTextAlignment(.center)
            Text("Your messages go to \(provider.label). Keys stay in this phone's Keychain.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Button("Open API keys", systemImage: "key") {
                apply(.openKeys)
            }
            .buttonStyle(.glass)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: Bottom stack

    private var hasAttention: Bool {
        store.recoveryPlan != nil || agent.pendingApproval != nil || !agent.watching.isEmpty
            || agent.chatProvider != nil || agent.approvedSpendUSD > 0
    }

    /// Everything needing attention scrolls inside a capped area; the composer
    /// below it never leaves the screen, however many cards there are.
    private var bottomStack: some View {
        GlassEffectContainer(spacing: 10) {
            VStack(spacing: 10) {
                if hasAttention {
                    ScrollView {
                        VStack(spacing: 10) {
                            if let plan = store.recoveryPlan { recoveryCard(plan) }
                            if let approval = agent.pendingApproval { approvalCard(approval) }
                            ForEach(agent.watching.prefix(3), id: \.self) { id in progressCard(id) }
                            if let used = agent.chatProvider { providerCaption(used) }
                            if agent.approvedSpendUSD > 0 { spendCaption }
                        }
                        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { attentionHeight = $0 }
                    }
                    .frame(height: min(attentionHeight, 320))
                    .scrollBounceBehavior(.basedOnSize)
                }
                if !keyMissing { composer }
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 8)
        }
    }

    /// A video that paused on an error, with the same choices the full-screen
    /// prompt would show - here, where the person already is. The choice is
    /// theirs; the assistant never picks one.
    private func recoveryCard(_ plan: RecoveryPlan) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(plan.title, systemImage: "exclamationmark.triangle.fill")
                .font(.subheadline.weight(.semibold)).foregroundStyle(Theme.bad)
            if let name = store.project(plan.projectID)?.title {
                Text("“\(name)” is paused at \(plan.phase.title).")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text(plan.reason).font(.footnote).foregroundStyle(.primary)
            if let pattern = plan.pattern {
                Label(pattern, systemImage: "lightbulb").font(.footnote).foregroundStyle(Theme.accent)
            }
            VStack(spacing: 6) {
                ForEach(plan.actions) { action in
                    Button {
                        Haptics.light()
                        store.resolveRecovery(action)
                        // These two leave the chat for another screen.
                        if action == .openKeys || action == .reword { agent.isOpen = false }
                    } label: {
                        HStack(spacing: 10) {
                            Image(systemName: action.symbol).frame(width: 22)
                            Text(action.label).font(.footnote.weight(.semibold))
                            Spacer(minLength: 0)
                        }
                        .foregroundStyle(action.isDestructive ? AnyShapeStyle(Theme.bad) : AnyShapeStyle(HierarchicalShapeStyle.primary))
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.glass)
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .liquidGlass(RoundedRectangle(cornerRadius: 24, style: .continuous), tint: Theme.bad.opacity(0.18))
    }

    private func approvalCard(_ a: AgentApproval) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(a.title).font(.headline).foregroundStyle(.primary)
            Text(a.detail).font(.subheadline).foregroundStyle(.secondary)
            // Only a video has a cost; a settings, channel or project change does not.
            if a.kind == .spend {
                Label("Estimated cost: \(a.estimate)", systemImage: "dollarsign.circle")
                    .font(.footnote).foregroundStyle(.primary)
            }
            HStack(spacing: 12) {
                Button("Decline") { Haptics.light(); agent.decline() }
                    .buttonStyle(.glass)
                Button(a.kind == .destructive ? "Delete" : "Approve") { Haptics.light(); agent.approve() }
                    .buttonStyle(.glassProminent)
                    .tint(a.kind == .destructive ? Theme.bad : nil)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .liquidGlass(RoundedRectangle(cornerRadius: 24, style: .continuous))
    }

    /// Live progress for a video this chat started: what it is doing right
    /// now, how far along, a Stop button - and a tap opens it in Studio.
    @ViewBuilder
    private func progressCard(_ id: String) -> some View {
        if let project = store.project(id) {
            let job = store.jobs[id]
            let place = store.queuePosition(id)
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(project.title).font(.footnote.weight(.semibold)).foregroundStyle(.primary).lineLimit(1)
                    Text(progressLine(job: job, place: place))
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    if let job {
                        ProgressView(value: min(max(job.progress, 0), 1)).tint(Theme.accent)
                    }
                }
                Spacer(minLength: 0)
                Button {
                    Haptics.light()
                    store.cancel(id)
                } label: {
                    Image(systemName: "stop.fill").font(.footnote)
                }
                .buttonStyle(.glass)
                .buttonBorderShape(.circle)
                .accessibilityLabel("Stop \(project.title)")
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .liquidGlass(RoundedRectangle(cornerRadius: 22, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
            .onTapGesture {
                store.selectForStudio(id)
                agent.isOpen = false
            }
            .accessibilityElement(children: .contain)
            .accessibilityAction(named: "Open in Studio") {
                store.selectForStudio(id)
                agent.isOpen = false
            }
        }
    }

    private func progressLine(job: JobState?, place: Int?) -> String {
        if let place { return "Queued, place \(place) - waiting for a free slot" }
        guard let job else { return "Starting…" }
        let pct = Int((min(max(job.progress, 0), 1) * 100).rounded())
        return "\(job.phase.title) · \(pct)%" + (job.message.isEmpty ? "" : " · \(job.message)")
    }

    private func providerCaption(_ kind: AgentProviderKind) -> some View {
        Text("Using \(kind.label) for this chat")
            .font(.caption2).foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 6)
    }

    private var spendCaption: some View {
        let cap = AgentSettingsRules.spendCap(store.settings.agentChatSpendCapUSD)
        return Text("Approved this chat: \(Agent.dollars(agent.approvedSpendUSD)) of \(Agent.dollars(cap))")
            .font(.caption2).foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 6)
    }

    // MARK: Composer

    /// A Liquid Glass input and a glass send button. They are siblings in the
    /// bottom stack's GlassEffectContainer, so the glass blends as they sit
    /// together, and the send button tints once there is something to send.
    private var composer: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Ask the assistant…", text: $draft, axis: .vertical)
                // The same size and font as the messages above it (the app font
                // setting otherwise makes the placeholder a different face).
                .font(.system(.subheadline))
                .lineLimit(1...5)
                .focused($focused)
                .padding(.horizontal, 16).padding(.vertical, 12)
                .liquidGlass(RoundedRectangle(cornerRadius: 22, style: .continuous))
            Button {
                if agent.isWorking { agent.stop() } else { send() }
            } label: {
                Image(systemName: agent.isWorking ? "stop.fill" : "arrow.up")
                    .font(.headline)
                    .foregroundStyle(canSend || agent.isWorking ? AnyShapeStyle(Color.white) : AnyShapeStyle(HierarchicalShapeStyle.secondary))
            }
            .buttonStyle(.glass)
            .buttonBorderShape(.circle)
            .controlSize(.large)
            .tint(canSend || agent.isWorking ? Theme.accent : nil)
            .disabled(!canSend && !agent.isWorking)
            .accessibilityLabel(agent.isWorking ? "Stop" : "Send")
        }
    }

    private func send() {
        let text = draft
        draft = ""
        Haptics.light()
        agent.send(text)
    }
}
