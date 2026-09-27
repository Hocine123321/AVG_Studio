import SwiftUI

struct WelcomeView: View {
    @Environment(Store.self) private var store
    @State private var channelName = "My Channel"
    @State private var niche = ""

    var body: some View {
        ScrollView {
            VStack(spacing: 22) {
                Image(systemName: "film.stack.fill")
                    .font(.system(size: 64))
                    .foregroundStyle(Theme.accent)
                    .padding(28)
                    .glassEffect(.regular.interactive(), in: .circle)
                    .padding(.top, 60)
                Text("AVG Studio").font(.largeTitle.weight(.heavy))
                Text("Write, illustrate, narrate and render videos — entirely on your iPhone.")
                    .multilineTextAlignment(.center).foregroundStyle(.secondary)

                VStack(alignment: .leading, spacing: 10) {
                    Text("Your first channel").font(.headline)
                    TextField("Channel name", text: $channelName)
                        .padding(12).glassEffect(.regular, in: .rect(cornerRadius: 14))
                    TextField("What's it about? (e.g. space facts)", text: $niche)
                        .padding(12).glassEffect(.regular, in: .rect(cornerRadius: 14))
                }
                .glassCard()

                VStack(alignment: .leading, spacing: 8) {
                    Label(AppleText.isAvailable ? "Apple Intelligence will write your scripts, free and offline." : "Scripts will be written by a free online service. Add your own keys anytime in Settings.", systemImage: "text.quote")
                    Label("Images from a free online service.", systemImage: "photo")
                    Label("Narration with Apple's built-in voices.", systemImage: "waveform")
                }
                .font(.callout)
                .glassCard()

                Button {
                    var c = store.activeChannel
                    c.name = channelName.trimmed.isEmpty ? "My Channel" : channelName.trimmed
                    c.niche = niche.trimmed
                    store.upsert(c)
                    store.activate(c)
                    if !AppleText.isAvailable { store.settings.text = .pollinations }
                    withAnimation(.spring) { store.settings.onboarded = true }
                } label: {
                    Text("Get started").font(.headline).frame(maxWidth: .infinity).padding(.vertical, 6)
                }
                .buttonStyle(.glassProminent)
            }
            .padding(20)
        }
        .background(AppBackground())
    }
}
