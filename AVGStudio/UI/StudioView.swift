import SwiftUI
import AVKit

struct StudioView: View {
    @Environment(Store.self) private var store
    @State private var player: AVPlayer?
    @State private var toastMessage: String?
    @State private var showLog = false
    @State private var showExportSheet = false
    @State private var logTail: [String] = []
    // CapCut export (ported from the PC's Slice 30 "My Videos" action -
    // see CapCutExporter's own doc comment for what iOS does differently).
    // `capcutBundle` doubles as the sheet's `isPresented` driver AND the
    // temp folder to clean up once that sheet goes away, in either
    // direction (completed share, or a plain swipe-to-dismiss).
    @State private var capcutBundle: URL?
    @State private var isExportingCapCut = false
    // A tap on a done/failed/pending phase row asks before redoing it -
    // the mock's own row tap opens a whole confirm-then-redo sheet, this
    // keeps that same one-extra-step safety net (never re-run a completed
    // phase - and its provider call - off a single accidental tap) with a
    // lighter confirmationDialog instead of a full second sheet type.
    @State private var confirmRedoPhase: PhaseID?

    var body: some View {
        // Studio is a persistent tab now (Skin v2), not a pushed screen -
        // it always shows Store.currentProject (the mock's own S.cur), not a
        // project id handed to it at construction time.
        if let p = store.currentProject {
            ScrollView {
                VStack(spacing: 16) {
                    header(p)
                    playerCard(p)
                    phasesCard(p)
                    liveListCard(p)
                    if !p.takes.isEmpty { takesCard(p) }
                    if !p.scenes.isEmpty { scenesCard(p) }
                    if let brief = p.brief {
                        DisclosureGroup("Creative brief") { Text(brief).font(.callout).frame(maxWidth: .infinity, alignment: .leading) }
                            .glassCard()
                    }
                }
                .padding(16)
            }
            .screenBackground()
            .navigationTitle(p.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                // Matches the PC's per-take "Log" toggle (Studio.tsx's
                // TakeLogToggle) - the FULL run history, not the mock's
                // small always-visible "Live list" tail (liveListCard,
                // below) which only shows the last few lines.
                ToolbarItem(placement: .topBarLeading) { AgentToolbarButton() }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Log", systemImage: "text.alignleft") { showLog = true }
                }
            }
            .onChange(of: p.output, initial: true) { _, _ in loadPlayer(p) }
            // Success feedback for a render finishing while this screen is
            // open - `initial: false` (the default) means old != new only
            // fires on a REAL change, never on first appearing at an
            // already-rendered project.
            .onChange(of: p.output) { old, new in if old != new, new != nil { Haptics.success() } }
            .toast($toastMessage)
            .sheet(isPresented: $showLog) { ProjectLogView(projectID: p.id, title: p.title) }
            .sheet(isPresented: $showExportSheet) { exportSheet(p) }
            // Keyed on running-ness too: the old `.task(id: p.id)` polled once
            // and quit if nothing was running yet, so starting a render from
            // this screen left the Live list frozen until you left and came back.
            .task(id: LogLoopKey(id: p.id, running: store.isRunning(p.id))) { await refreshLogLoop(p.id) }
            .onDisappear { player?.pause() }
            .sheet(isPresented: Binding(
                get: { capcutBundle != nil },
                set: { if !$0 { discardCapcutBundle() } }
            )) {
                if let url = capcutBundle {
                    ActivityShareSheet(items: [url]) { discardCapcutBundle() }
                }
            }
        } else {
            ContentUnavailableView {
                Label("No videos yet", systemImage: "clapperboard")
            } description: {
                Text("Create your first video and it'll show up here.")
            } actions: {
                Button("Create a video") { store.tab = .create }.buttonStyle(.glassProminent)
            }
            .screenBackground()
        }
    }

    private func loadPlayer(_ p: Project) {
        if let url = store.file(p, p.output), FileManager.default.fileExists(atPath: url.path) {
            player = AVPlayer(url: url)
        } else { player = nil }
    }

    private func refreshLogLoop(_ id: String) async {
        logTail = recentLog(id)
        while store.isRunning(id), !Task.isCancelled {
            try? await Task.sleep(for: .seconds(1))
            logTail = recentLog(id)
        }
    }

    private func recentLog(_ id: String) -> [String] {
        Array(store.projectLogTail(id, lines: 6).lines.reversed())
    }

    /// The mock's own `.row` under its `.lt` title: title + a one-line
    /// "format, status" subtitle. This screen still uses the system
    /// `.navigationTitle` for the title itself (the custom floating
    /// scroll-reveal title bar is deferred - see SKIN_V2_PLAN.md), so this
    /// row supplies just the subtitle half.
    @ViewBuilder private func header(_ p: Project) -> some View {
        Text(statusLine(p))
            .font(.system(size: 12.5)).foregroundStyle(Theme.ink2)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 4)
    }

    private func statusLine(_ p: Project) -> String {
        let status: String
        switch p.overallStatus {
        case .running: status = "rendering"
        case .done: status = "ready to export"
        case .failed: status = "render failed"
        case .pending: status = "not started yet"
        }
        return "\(p.aspectRatio.rawValue), \(status)"
    }

    @ViewBuilder private func playerCard(_ p: Project) -> some View {
        VStack(spacing: 12) {
            ZStack {
                if let player {
                    VideoPlayer(player: player)
                } else {
                    Rectangle().fill(.black.opacity(0.35))
                    if let job = store.jobs[p.id] {
                        VStack(spacing: 10) {
                            ProgressView(value: job.progress).tint(Theme.accent).frame(width: 180)
                            Text(job.message).font(.footnote).foregroundStyle(.white)
                        }
                    } else {
                        Image(systemName: "play.slash").font(.largeTitle).foregroundStyle(.white.opacity(0.7))
                    }
                }
            }
            .aspectRatio(p.aspectRatio == .portrait ? 9.0 / 16.0 : (p.aspectRatio == .landscape ? 16.0 / 9.0 : 1), contentMode: .fit)
            .frame(maxHeight: 460)
            .clipShape(.rect(cornerRadius: 26))

            HStack(spacing: 12) {
                if store.isRunning(p.id) {
                    Button("Cancel", systemImage: "stop.fill", role: .destructive) {
                        Haptics.light()
                        store.cancel(p.id)
                    }
                    .buttonStyle(.glass)
                } else {
                    Button(p.output == nil ? "Render" : "Render again", systemImage: "play.fill") {
                        Haptics.light()
                        store.start(p.id, from: p.narration == nil ? .writing : firstIncomplete(p))
                    }
                    .buttonStyle(.glass)
                }
                // The mock's own `.btns` row: Cancel (while live) + one
                // prominent Export action - Save-to-Photos/CapCut/Share
                // are its rows now (exportSheet below), not separate
                // buttons scattered across this card and the toolbar.
                Button("Export", systemImage: "square.and.arrow.up") {
                    Haptics.light()
                    showExportSheet = true
                }
                .buttonStyle(.glassProminent)
                .disabled(store.file(p, p.output) == nil)
            }

            if p.estimatedCostUSD > 0 {
                HStack {
                    Image(systemName: "dollarsign.circle").foregroundStyle(.secondary)
                    Text("~$\(String(format: "%.3f", p.estimatedCostUSD)) so far · rough estimate, not a real invoice")
                        .font(.caption2).foregroundStyle(.secondary)
                    Spacer()
                }
            }
        }
        .glassCard(30, padding: 12)
    }

    private func firstIncomplete(_ p: Project) -> PhaseID {
        PhaseID.allCases.first { p.phase($0).status != .done } ?? .render
    }

    /// The mock's `.ph.g` rows: each phase is its OWN floating glass card
    /// (not one shared card with hairline dividers), an icon circle that
    /// fills solid + pulses while running, and a trailing Done/percent
    /// readout. Kept at the real app's 5 stored `PhaseID`s, not the
    /// mock's 8 (Brief/Script/Director I/Director II live only as
    /// progress-message text inside `.writing` - see SKIN_V2_PLAN.md's
    /// "known mismatches").
    @ViewBuilder private func phasesCard(_ p: Project) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Pipeline").sectionHeader().padding(.leading, 6)
            ForEach(PhaseID.allCases) { ph in
                phaseRow(p, ph)
            }
        }
        .confirmationDialog(
            "Redo from \(confirmRedoPhase?.title ?? "")?",
            isPresented: Binding(get: { confirmRedoPhase != nil }, set: { if !$0 { confirmRedoPhase = nil } }),
            titleVisibility: .visible
        ) {
            Button("Redo", role: .destructive) {
                if let ph = confirmRedoPhase { store.start(p.id, from: ph) }
                confirmRedoPhase = nil
            }
            Button("Cancel", role: .cancel) { confirmRedoPhase = nil }
        } message: {
            Text("Runs \(confirmRedoPhase?.title ?? "this phase") again and everything after it in this take.")
        }
    }

    @ViewBuilder private func phaseRow(_ p: Project, _ ph: PhaseID) -> some View {
        let r = p.phase(ph)
        HStack(spacing: 12) {
            PhaseIcon(status: r.status)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(ph.title).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.ink)
                    if ph == .captions, r.status == .done, !p.cards.isEmpty {
                        Text("\(p.cards.count) card\(p.cards.count == 1 ? "" : "s")")
                            .font(.caption2.weight(.semibold)).foregroundStyle(Theme.accent)
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(Theme.accent.opacity(0.15), in: .capsule)
                    }
                }
                if let e = r.error, r.status == .failed {
                    Text(e).font(.caption).foregroundStyle(Theme.bad).lineLimit(3)
                } else if r.status == .running, let j = store.jobs[p.id] {
                    Text(j.message).font(.caption).foregroundStyle(Theme.ink2).lineLimit(2)
                } else {
                    Text(phaseSubtitle(ph, p)).font(.caption).foregroundStyle(Theme.ink2)
                }
                if r.status == .running, let j = store.jobs[p.id] {
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            Capsule().fill(Theme.fill)
                            Capsule().fill(Theme.accent).frame(width: geo.size.width * max(0, min(1, j.progress)))
                        }
                    }
                    .frame(height: 5)
                }
            }
            Spacer(minLength: 8)
            trailingText(r.status, p, ph)
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .contentShape(.rect)
        .glass(RoundedRectangle(cornerRadius: 22, style: .continuous))
        .onTapGesture {
            guard r.status != .running, !store.isRunning(p.id) else { return }
            Haptics.light()
            confirmRedoPhase = ph
        }
        // A bare tap gesture is invisible to VoiceOver; announce it as the
        // button it is.
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint(r.status == .running ? "" : "Redo from this step")
    }

    /// Real per-phase provider info in place of the mock's static table
    /// (its "Groq, llama-3.3-70b" strings are demo copy for a fixed
    /// pipeline - this app's writer/image/voice providers are user
    /// settings, and Captions genuinely does switch between on-device
    /// estimation and AssemblyAI depending on `captionsSource`).
    private func phaseSubtitle(_ ph: PhaseID, _ p: Project) -> String {
        switch ph {
        case .writing: return store.settings.text.label
        case .visuals: return store.settings.image.label
        case .voice: return store.settings.voice.label
        case .captions: return p.captionsSource == "assemblyai" ? "AssemblyAI" : "On-device, estimated timing"
        case .render: return "On-device, AVFoundation (HEVC)"
        }
    }

    @ViewBuilder private func trailingText(_ status: PhaseStatus, _ p: Project, _ ph: PhaseID) -> some View {
        switch status {
        case .done: Text("Done").font(.caption.weight(.semibold)).foregroundStyle(Theme.ink2)
        case .running:
            if let j = store.jobs[p.id] {
                Text("\(Int(max(0, min(1, j.progress)) * 100))%").font(.caption.weight(.semibold)).foregroundStyle(Theme.accent)
            }
        case .failed: Text("Failed").font(.caption.weight(.semibold)).foregroundStyle(Theme.bad)
        // No fabricated per-phase cost table (the mock's own "Free"/"about
        // $0.12" is a fixed demo estimate) - a plain dash until it runs.
        case .pending: Text("—").font(.caption).foregroundStyle(Theme.ink2)
        }
    }

    /// The mock's always-visible "Live list" card - the tail of this
    /// project's real run log, not the mock's fixed 5-line demo array.
    /// The toolbar's "Log" button (above) still opens the FULL history;
    /// this is just the at-a-glance recent tail, additive to that.
    @ViewBuilder private func liveListCard(_ p: Project) -> some View {
        if !logTail.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("Live list").sectionHeader().padding(.leading, 6)
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(logTail.enumerated()), id: \.offset) { _, line in
                        Text(line)
                            .font(.system(.footnote, design: .monospaced))
                            .foregroundStyle(Theme.ink2)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .lineLimit(2)
                    }
                }
                .glassCard(20, padding: 14)
            }
        }
    }

    @ViewBuilder private func takesCard(_ p: Project) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Takes").sectionHeader()
                Spacer()
                if isExportingCapCut { ProgressView().controlSize(.small) }
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(p.takes.reversed()) { take in
                        Button(take.label, systemImage: take.file == p.output ? "checkmark.circle.fill" : "film") {
                            store.update(p.id) { $0.output = take.file }
                        }
                        .buttonStyle(.glass)
                        .disabled(isExportingCapCut)
                        .contextMenu {
                            Button("Export to CapCut", systemImage: "square.and.arrow.up") {
                                exportToCapCut(p, take)
                            }
                        }
                    }
                }
            }
        }
        .glassCard()
    }

    private func exportToCapCut(_ p: Project, _ take: Take) {
        guard !isExportingCapCut else { return }
        isExportingCapCut = true
        Task {
            defer { isExportingCapCut = false }
            do {
                let url = try await CapCutExporter.export(project: p, take: take, store: store)
                capcutBundle = url
                Haptics.success()
            } catch {
                Haptics.error()
                store.record(p.id, "CapCut export failed: \(error.localizedDescription)", error: true)
            }
        }
    }

    private func discardCapcutBundle() {
        if let url = capcutBundle { try? FileManager.default.removeItem(at: url) }
        capcutBundle = nil
    }

    @ViewBuilder private func scenesCard(_ p: Project) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Scenes").sectionHeader().padding(.leading, 6)
            VStack(alignment: .leading, spacing: 12) {
                ForEach(p.scenes) { s in
                    HStack(alignment: .top, spacing: 12) {
                        ZStack {
                            Rectangle().fill(.quaternary)
                            CachedThumb(url: store.file(p, s.imageFile), maxPixel: 200)
                        }
                        .frame(width: 64, height: 96).clipShape(.rect(cornerRadius: 12))
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Scene \(s.index)").font(.caption.weight(.bold)).foregroundStyle(Theme.accent)
                            Text(Pipeline.spoken(s.text)).font(.callout)
                        }
                    }
                }
            }
            .glassCard()
        }
    }

    /// The mock's Export sheet: three rows (Save/CapCut/Share). All three
    /// are real actions here (the mock's own rows are demo toasts for
    /// every option, including the two this app actually implements); the
    /// third is relabelled "Share video" rather than the mock's "Share
    /// link" - this app shares the local file via the system share sheet,
    /// it doesn't host a web link anywhere.
    @ViewBuilder private func exportSheet(_ p: Project) -> some View {
        GlassSheet(title: "Export") {
            VStack(spacing: 0) {
                if let url = store.file(p, p.output) {
                    exportRow("Save to Photos", "square.and.arrow.down") {
                        showExportSheet = false
                        Task {
                            do {
                                try await PhotoSaver.saveVideo(at: url)
                                Haptics.success()
                                toastMessage = "Saved to Photos"
                            } catch {
                                Haptics.error()
                                toastMessage = error.localizedDescription
                            }
                        }
                    }
                    if let take = p.takes.first(where: { $0.file == p.output }) {
                        exportRow("Send as CapCut draft", "scissors") {
                            showExportSheet = false
                            exportToCapCut(p, take)
                        }
                    }
                    // Left presenting (not dismissed on tap): ShareLink
                    // stacks its own system share sheet on top of this one
                    // rather than needing it closed first - dismissing
                    // this sheet at the same moment risked the share sheet
                    // losing its presenting view controller mid-animation.
                    ShareLink(item: url) {
                        exportRowLabel("Share video", "square.and.arrow.up")
                    }
                } else {
                    Text("Nothing to export yet - render this video first.")
                        .font(.callout).foregroundStyle(Theme.ink2)
                        .padding(.vertical, 12)
                }
            }
        }
    }

    @ViewBuilder private func exportRow(_ title: String, _ symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) { exportRowLabel(title, symbol) }
    }

    @ViewBuilder private func exportRowLabel(_ title: String, _ symbol: String) -> some View {
        HStack {
            Image(systemName: symbol).foregroundStyle(Theme.accent).frame(width: 24)
            Text(title).foregroundStyle(Theme.ink)
            Spacer()
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Theme.ink2)
        }
        .padding(.vertical, 12)
        .contentShape(.rect)
    }
}

/// Identity for Studio's Live-list polling task: it must restart when the
/// project changes OR when a run starts/stops.
private struct LogLoopKey: Hashable {
    let id: String
    let running: Bool
}

/// The mock's `.pi`: a flat icon circle that fills solid + pulses while
/// `.running` (`@keyframes pulse` - an outward ring, respected for Reduce
/// Motion). `.failed` isn't in the mock's own demo state machine at all
/// (its PH rows never fail) but the real pipeline can, so this adds a red
/// fill for that case rather than leaving it unstyled.
private struct PhaseIcon: View {
    let status: PhaseStatus
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pulse = false

    var body: some View {
        ZStack {
            Circle().fill(fill)
            if status == .pending {
                Circle().strokeBorder(Theme.glassEdge, lineWidth: 0.5)
            }
            if status == .running && !reduceMotion {
                Circle().strokeBorder(Theme.accent.opacity(0.6), lineWidth: 2)
                    .scaleEffect(pulse ? 1.7 : 1)
                    .opacity(pulse ? 0 : 1)
            }
            glyph
        }
        .frame(width: 34, height: 34)
        .onAppear {
            guard status == .running, !reduceMotion else { return }
            withAnimation(.easeOut(duration: 1.6).repeatForever(autoreverses: false)) { pulse = true }
        }
    }

    @ViewBuilder private var glyph: some View {
        switch status {
        case .done: Image(systemName: "checkmark").font(.system(size: 14, weight: .bold)).foregroundStyle(Theme.onOk)
        case .running: Image(systemName: "waveform.path.ecg").font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.onAccent)
        case .failed: Image(systemName: "exclamationmark").font(.system(size: 14, weight: .bold)).foregroundStyle(.white)
        case .pending: EmptyView()
        }
    }

    private var fill: Color {
        switch status {
        case .done: return Theme.ok
        case .running: return Theme.accent
        case .failed: return Theme.bad
        case .pending: return Theme.fill
        }
    }
}
