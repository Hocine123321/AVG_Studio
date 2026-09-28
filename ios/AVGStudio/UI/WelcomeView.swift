import SwiftUI

private enum WelcomeField: Hashable { case name, niche }

struct WelcomeView: View {
    @Environment(Store.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var channelName = "My Channel"
    @State private var niche = ""
    @FocusState private var field: WelcomeField?
    @State private var appeared = false

    var body: some View {
        ScrollView {
            VStack(spacing: 24) {
                header
                firstChannelCard
                whatYouGetCard
            }
            .padding(20)
            .padding(.bottom, 8)
        }
        .scrollDismissesKeyboard(.interactively)
        .scrollContentBackground(.hidden)
        .safeAreaInset(edge: .bottom) { ctaBar }
        .background(AppBackground())
        .onAppear {
            // A first run only ever appears once per install - the reveal
            // is a one-time welcome, not something worth re-triggering, so
            // it's guarded to fire exactly once even if this view's identity
            // gets re-evaluated. Reduce Motion skips straight to the settled
            // state instead of leaving the screen invisible for anyone who
            // has that setting on for a reason.
            guard !appeared else { return }
            if reduceMotion { appeared = true } else {
                withAnimation(.spring(response: 0.5, dampingFraction: 0.86)) { appeared = true }
            }
        }
    }

    private var header: some View {
        VStack(spacing: 12) {
            ZStack {
                Circle().fill(Theme.accent.opacity(0.35)).frame(width: 132, height: 132).blur(radius: 28)
                Image(systemName: "film.stack.fill")
                    .font(.system(size: 60))
                    .foregroundStyle(Theme.accent)
                    .padding(26)
                    .glass(Circle())
            }
            .padding(.top, 12)

            Text("WELCOME")
                .font(.caption.weight(.bold))
                .foregroundStyle(.secondary)
                .kerning(1.4)
            Text("AVG Studio").font(.largeTitle.weight(.heavy))
            Text("Write, illustrate, narrate and render videos — entirely on your iPhone.")
                .font(.callout)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 6)
        }
        .opacity(appeared ? 1 : 0)
        .offset(y: appeared ? 0 : 14)
        .animation(.spring(response: 0.5, dampingFraction: 0.86).delay(0.05), value: appeared)
    }

    private var firstChannelCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Your first channel").font(.headline)
            TextField("Channel name", text: $channelName)
                .focused($field, equals: .name)
                .submitLabel(.next)
                .textInputAutocapitalization(.words)
                .onSubmit { field = .niche }
                .fieldStyle(14)
            TextField("What's it about? (e.g. space facts)", text: $niche)
                .focused($field, equals: .niche)
                .submitLabel(.done)
                .onSubmit { field = nil }
                .fieldStyle(14)
        }
        .glassCard()
        .opacity(appeared ? 1 : 0)
        .offset(y: appeared ? 0 : 14)
        .animation(.spring(response: 0.5, dampingFraction: 0.86).delay(0.12), value: appeared)
    }

    private var whatYouGetCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("What you'll get").font(.headline)
            featureRow(
                icon: "text.quote", tint: Theme.accent,
                text: AppleText.isAvailable
                    ? "Apple Intelligence will write your scripts, free and offline."
                    : "Scripts will be written by a free online service. Add your own keys anytime in Settings."
            )
            // Single accent throughout, not a tint per row - the mock's
            // own palette has no second/third bright hue to spend on this.
            featureRow(icon: "photo", tint: Theme.accent, text: "Images from a free online service.")
            featureRow(icon: "waveform", tint: Theme.accent, text: "Narration with Apple's built-in voices.")
        }
        .glassCard()
        .opacity(appeared ? 1 : 0)
        .offset(y: appeared ? 0 : 14)
        .animation(.spring(response: 0.5, dampingFraction: 0.86).delay(0.18), value: appeared)
    }

    @ViewBuilder private func featureRow(icon: String, tint: Color, text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(tint)
                .frame(width: 30, height: 30)
                .background(tint.opacity(0.18), in: .circle)
            Text(text).font(.callout).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var ctaBar: some View {
        Button {
            var c = store.activeChannel
            c.name = channelName.trimmed.isEmpty ? "My Channel" : channelName.trimmed
            c.niche = niche.trimmed
            store.upsert(c)
            store.activate(c)
            if !AppleText.isAvailable { store.settings.text = .pollinations }
            field = nil
            withAnimation(.spring) { store.settings.onboarded = true }
        } label: {
            Text("Get started").font(.headline).frame(maxWidth: .infinity).padding(.vertical, 6)
        }
        .buttonStyle(.glassProminent)
        .padding(.horizontal, 20)
        .padding(.top, 10)
        .padding(.bottom, 8)
        .opacity(appeared ? 1 : 0)
        .offset(y: appeared ? 0 : 10)
        .animation(.spring(response: 0.5, dampingFraction: 0.86).delay(0.24), value: appeared)
    }
}
