import SwiftUI
import AVKit

struct StudioView: View {
    @Environment(Store.self) private var store
    let projectID: String
    @State private var player: AVPlayer?
    @State private var savedToPhotos = false
    @State private var showLog = false
    // CapCut export (ported from the PC's Slice 30 "My Videos" action -
    // see CapCutExporter's own doc comment for what iOS does differently).
    // `capcutBundle` doubles as the sheet's `isPresented` driver AND the
    // temp folder to clean up once that sheet goes away, in either
    // direction (completed share, or a plain swipe-to-dismiss).
    @State private var capcutBundle: URL?
    @State private var isExportingCapCut = false

    var body: some View {
        if let p = store.project(projectID) {
            ScrollView {
                VStack(spacing: 16) {
                    playerCard(p)
                    phasesCard(p)
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
                // TakeLogToggle) - everything the run has done for THIS
                // video, tail-read from `Store.projectLogTail`, available
                // in every phase state (pending/running/failed/done): the
                // PC's own doc comment calls out a log being most worth
                // checking exactly when a phase failed before writing any
                // artifact, which is precisely when nothing else on this
                // screen has anything to show.
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Log", systemImage: "text.alignleft") { showLog = true }
                }
                if let url = store.file(p, p.output) {
                    ToolbarItem(placement: .topBarTrailing) { ShareLink(item: url) }
                }
            }
            .onChange(of: p.output, initial: true) { _, _ in loadPlayer(p) }
            // Success feedback for a render finishing while this screen is
            // open - `initial: false` (the default) means old != new only
            // fires on a REAL change, never on first appearing at an
            // already-rendered project.
            .onChange(of: p.output) { old, new in if old != new, new != nil { Haptics.success() } }
            .alert("Saved to Photos", isPresented: $savedToPhotos) { Button("OK") {} }
            .sheet(isPresented: $showLog) { ProjectLogView(projectID: projectID, title: p.title) }
        } else {
            ContentUnavailableView("Project not found", systemImage: "questionmark.folder")
        }
    }

    private func loadPlayer(_ p: Project) {
        if let url = store.file(p, p.output), FileManager.default.fileExists(atPath: url.path) {
            player = AVPlayer(url: url)
        } else { player = nil }
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
            .clipShape(.rect(cornerRadius: 20))

            GlassEffectContainer(spacing: 12) {
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
                        .buttonStyle(.glassProminent)
                    }
                    if let url = store.file(p, p.output) {
                        Button("Save", systemImage: "square.and.arrow.down") {
                            UISaveVideoAtPathToSavedPhotosAlbum(url.path, nil, nil, nil)
                            Haptics.success()
                            savedToPhotos = true
                        }
                        .buttonStyle(.glass)
                    }
                }
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

    @ViewBuilder private func phasesCard(_ p: Project) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Pipeline").font(.headline)
            ForEach(PhaseID.allCases) { ph in
                let r = p.phase(ph)
                HStack(spacing: 12) {
                    Image(systemName: ph.symbol).frame(width: 28)
                        .foregroundStyle(r.status == .done ? .green : (r.status == .failed ? .red : .secondary))
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(ph.title).font(.subheadline.weight(.medium))
                            if ph == .captions, r.status == .done {
                                Text(p.captionsSource == "assemblyai" ? "AssemblyAI" : "Estimated")
                                    .font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                                    .padding(.horizontal, 6).padding(.vertical, 2)
                                    .glassEffect(.regular, in: .capsule)
                                if !p.cards.isEmpty {
                                    Text("\(p.cards.count) card\(p.cards.count == 1 ? "" : "s")")
                                        .font(.caption2.weight(.semibold)).foregroundStyle(Theme.accent)
                                        .padding(.horizontal, 6).padding(.vertical, 2)
                                        .glassEffect(.regular.tint(Theme.accent.opacity(0.25)), in: .capsule)
                                }
                            }
                        }
                        if let e = r.error, r.status == .failed { Text(e).font(.caption).foregroundStyle(.red).lineLimit(3) }
                        else if r.status == .running, let j = store.jobs[p.id] { Text(j.message).font(.caption).foregroundStyle(.secondary) }
                    }
                    Spacer()
                    if r.status == .running { ProgressView() }
                    else {
                        Menu {
                            Button("Redo from \(ph.title)", systemImage: "arrow.clockwise") { store.start(p.id, from: ph) }
                                .disabled(store.isRunning(p.id))
                        } label: {
                            Image(systemName: r.status == .done ? "checkmark.circle.fill" : "ellipsis.circle")
                                .foregroundStyle(r.status == .done ? .green : .secondary)
                        }
                    }
                }
            }
        }
        .glassCard()
    }

    @ViewBuilder private func takesCard(_ p: Project) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Takes").font(.headline)
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
        .sheet(isPresented: Binding(
            get: { capcutBundle != nil },
            set: { if !$0 { discardCapcutBundle() } }
        )) {
            if let url = capcutBundle {
                ActivityShareSheet(items: [url]) { discardCapcutBundle() }
            }
        }
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
            Text("Scenes").font(.headline)
            ForEach(p.scenes) { s in
                HStack(alignment: .top, spacing: 12) {
                    Group {
                        if let u = store.file(p, s.imageFile), let img = UIImage(contentsOfFile: u.path) {
                            Image(uiImage: img).resizable().scaledToFill()
                        } else {
                            Rectangle().fill(.quaternary)
                        }
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
