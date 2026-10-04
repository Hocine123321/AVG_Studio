# In-App Video Agent Implementation Plan (v2: with Assistant settings)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A chat sheet inside AVG Studio where the user says "make a 9:16 video about X" and a cloud model (Groq first, Gemini second) drives the app's existing `Store` to create, monitor, redo, cancel and open videos. A new **Assistant** settings page controls which model it uses, what it may do, when it must ask, how much it may spend, how it talks, whether it remembers, and what standing instructions it follows.

**Architecture:** A main-actor `Agent`, owned by `Store`, runs a bounded tool-calling loop over an `AgentProvider` protocol (Groq in OpenAI format, Gemini in `functionDeclarations` format). Tools are thin wrappers over members `Store` already has, reached through an `AgentHost` protocol so the loop and the tools are unit-testable with a fake host. Every **policy** decision (may it do this, must it ask, is it over the spend limit) is plain Swift in `AgentPolicy`, driven by `AppSettings`. Nothing the model writes, and nothing in the custom instructions, can switch a policy off.

**Tech Stack:** Swift (language mode 5.0), SwiftUI + Observation, `URLSession` through the existing `HTTP` layer, XCTest, XcodeGen, GitHub Actions on `macos-26`.

**Spec:** the "Spec and research findings" section of this file. The iOS repo has no `docs/` tree (its plans live at the root, like `SKIN_V2_PLAN.md`), so there is no separate spec file.

## Global Constraints

- Deployment target iOS 26.0, Swift language mode 5.0, iPhone only, portrait (`project.yml`).
- **There is no local Swift toolchain.** Nothing "builds" or "passes" until CI says so. Until then report "read and grepped, compile unverified".
- API keys: only through `Keychain.require` / `Keychain.has`, sent in a **header** (`Authorization: Bearer …` for Groq, `x-goog-api-key` for Gemini). Never in a URL, log line, error text, `UserDefaults`, `project.json`, `.avg-meta.json`, the saved chat file or the transcript.
- Every `HTTP.send` / `HTTP.retrying` names `provider:` (`"groq"`, `"gemini"`). No retry or sleep outside `HTTP.retrying`. No `print` / `NSLog`; log with `store.record`.
- Nothing below `Store` touches UI state. The agent calls only members `Store` already exposes. `Core/` imports no UIKit (haptics are fired by the view layer).
- **Policy lives in code.** Whether an action needs a tap, is allowed, or is over the spend limit is decided by `AgentPolicy` from `AppSettings`. It never reads model output or the custom instructions.
- **When in doubt, ask.** A cost the estimate cannot price counts as paid. `CostRates` switches are exhaustive with no `default:`, so a new provider kind cannot silently be free.
- **At most one auto-started action per user message.** Approved actions are not limited this way (each is a tap).
- A failed phase still pauses on the `RecoverySheet`. The agent reports it and never picks a recovery action.
- Colours, radii, fonts from `UI/Theme.swift`; one `.glassProminent` button per screen; status colour via `Theme.ok` / `Theme.bad` / `Theme.accent`; honour `accessibilityReduceMotion`.
- A new setting is: field with default, a line in the tolerant `init(from:)` (`c.value("key", key)`), a control, a `SettingAnchor`, a `SettingsIndex` entry with a unique title.
- Add or update the test in the same commit as any pure helper. No test needs the network, a real key or a device.
- Exactly one `Store`. The agent never constructs another.

## Review Focus

The inputs most likely to bite a person using this, most likely first. Each has a pinning test in the task named.

1. **The model names a project by title, or by a ref that matches two.** Expect: an error listing the candidates, never a guess. (Task 5)
2. **The model sends arguments as the wrong type** (`"limit":"3"`, `"flag":"true"`, malformed JSON). Expect: tolerant parsing, a clear tool error, no crash. (Task 1)
3. **History is trimmed or compacted through the middle of a tool exchange.** Groq answers 400 to a `tool` message with no matching assistant `tool_calls`. Expect: the window always starts on a user message and a tool message never outlives its call. (Task 1)
4. **"Only ask for paid videos" starts something that costs money** because the estimate said free. Expect: unknown or zero-rate-missing components count as paid, and only an exactly-zero estimate skips the tap. (Tasks 4, 7)
5. **The spend limit is dodged through `redo_from`**, or an approval is tapped twice. Expect: redo is priced from its phase onward and counted; the second tap does nothing. (Tasks 4, 6, 7)
6. **Failover runs after a tool already ran**, so a video is started twice. Expect: failover only before the first tool call of a message. (Task 12)
7. **The writer is Apple Intelligence (this phone cannot run it) or the chosen provider has no key, and the user asks for a video.** Expect: a readable refusal before any project exists. (Task 5)

---

## Spec and research findings

**What is being built.** Chat with an agent that uses the app. v1 tools: `list_channels`, `list_projects`, `get_status`, `create_video` (gated), `redo_from` (gated), `cancel`, `open_project`. First provider Groq, second Gemini (Task 11).

**Why a cloud model.** The user's iPhone 12 has no Apple Intelligence, so `AppleText` is unavailable. The app already holds Groq and Gemini keys in the Keychain, and both support function calling.

### The Assistant settings (new, all in `AppSettings`)

| Setting (UI title) | Key | Default | What it does | Built in |
|---|---|---|---|---|
| Assistant provider | `agentProvider` | `groq` | Which service runs the chat (`groq`/`gemini`) | 2, 3, 11 |
| Assistant model | `agentGroqModel`, `agentGeminiModel` | `llama-3.3-70b-versatile`, `gemini-2.5-flash` | Model per provider, picked from the same catalogues as the writer | 2, 10 |
| Test assistant | none (an action) | n/a | One tiny request that proves the key works **and** the model can call tools | 6, 10 |
| Switch assistant on limits | `agentFailover` | `true` | If this provider is rate-limited or out of quota, try the other one (if its key exists) | 12 |
| Assistant can start videos | `agentAllowCreate` | `true` | Off: `create_video` is not even offered to the model | 5, 10 |
| Assistant can redo steps | `agentAllowRedo` | `true` | Same, for `redo_from` | 5, 10 |
| Assistant can cancel | `agentAllowCancel` | `true` | Same, for `cancel` | 5, 10 |
| Ask before starting | `agentApproval` | `always` | `always`: every start/redo shows Approve/Decline. `paidOnly`: free ones start without a tap; paid ones always ask | 7, 10 |
| Spend limit per chat | `agentChatSpendCapUSD` | `1.00` ($0.25 to $10, step $0.25) | The assistant refuses to propose more paid work once approved estimates would pass this. Resets on New chat and on relaunch | 7, 10 |
| Reply length | `agentReplyStyle` | `brief` | `brief`/`normal`: changes the system prompt (phone-sized or fuller replies) | 7, 10 |
| Announce finished videos | `agentAnnounceResults` | `true` | Posts "ready" or "stopped at <phase>" in the chat, with a haptic, for videos the assistant started. Costs no tokens | 7, 9, 10 |
| Keep chat | `agentKeepChat` | `false` | Saves the conversation (your messages and its answers only) across launches | 8, 10 |
| Clear saved chat | none (an action) | n/a | Deletes the saved file | 8, 10 |
| Custom instructions | `agentInstructions` | `""` (max 400 characters) | Standing preferences added to the system prompt ("use my Facts channel", "titles under 6 words") | 7, 10 |

**Considered and left as constants, not settings:** step cap per message (8), history window (24 messages), temperature (0.2), reply ceiling (4096 tokens), per-tool toggles for read-only tools, default channel (the message names one, else the active channel), notifications and voice input (out of scope).

### Research

| Finding | Source / date checked | Consequence |
|---|---|---|
| Groq: tool definitions go in `tools`; the model returns `tool_calls` with an `id`, `function.name` and `function.arguments` as a JSON **string**; the result goes back as `role:"tool"` with the matching `tool_call_id`. | console.groq.com/docs/tool-use/overview, Oct 2026 | Codec in Task 3. |
| Groq request fields: `max_completion_tokens` (`max_tokens` is **deprecated**), `parallel_tool_calls` (default **true**), `tool_choice` (`none`/`auto`/`required`/a named tool), `temperature`. Groq's own tool-calling example keeps temperature low and uses 4096 completion tokens. | console.groq.com/docs/api-reference and /tool-use/local-tool-calling | Send `max_completion_tokens: 4096`, `parallel_tool_calls: false`, `tool_choice: "auto"`, `temperature: 0.2`. |
| Groq tool-use table: `openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, `minimaxai/minimax-m2.7`, `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`. gpt-oss models do not do parallel tool use. | tool-use overview | Default model is `llama-3.3-70b-versatile`. The model picker shows a "known to support tools" footnote. |
| Groq free plan, rows I could read: 30 RPM, 1K RPD, **8K TPM**, 200K TPD for gpt-oss-20b/120b and qwen3.8-27b. The `llama-3.3-70b-versatile` row did not render. Limits are per **organization**. | console.groq.com/docs/rate-limits | Keep the system prompt, the tool schemas and the history small (Tasks 1, 5, 6, 7), and add a token-budget test. The writer shares these limits. Read your real numbers at console.groq.com/settings/limits. |
| Groq answers **HTTP 400, `code: "tool_use_failed"`, with `failed_generation`** when the model's tool call is malformed. Reports say it happens from time to time; one has llama-3.3-70b near 100% success while Llama 4 models failed every time. Some models answer 400 "tool calling is not supported with this model". | Groq forum and GitHub issues | `classify()` maps this to `.bug` today (never retried). Task 3 adds `ErrorKind.malformedOutput`. The loop answers it with a nudge to the model (Task 6). The **Test assistant** button (Tasks 6, 10) exists so a model that cannot call tools is found in Settings, not mid-chat. |
| The app's `ErrorKind` has exactly: `auth, quota, billing, rateLimit, modelMissing, contentFilter, network, timeout, dependency, bug`. `rateLimit` gets the deepest retry budget. | `Providers/ErrorPolicy.swift` | Failover (Task 12) triggers on `rateLimit`, `quota`, `billing`, `network`, `timeout`. Because the `rateLimit` budget is deep, a chat can sit in backoff for a while; Task 12 handles this. |
| Gemini: `tools:[{functionDeclarations:[…]}]`; replies carry `functionCall{id,name,args}` in `parts` (do not assume position); results go back in a `role:"user"` content as `functionResponse{id,name,response}`. Gemini 3 always returns an `id` and it must be echoed. | ai.google.dev function-calling docs, updated 2026-09-16 | Codec in Task 11. |
| **Gemini 3 requires thought signatures**: the model's `functionCall` part must be sent back exactly as returned, or the API answers 400. Older models (2.5) are not strict. | ai.google.dev thought-signatures docs | `AgentMessage.providerPayload` stores the model turn's raw JSON; the Gemini codec replays it verbatim. Compaction and saved chats keep **text only**, so no stale payload ever travels. |
| Gemini guidance: keep the active tool set to 10–20 at most, low temperature, validate consequential calls with the user, check `finishReason` (a malformed call shows up as `Malformed_Function_Call`). | same | 7 tools, temperature 0.2, approval gate, map the finish reason to `.malformedOutput`. |
| Gemini limits are shown only in AI Studio, are per project not per key, and "are not guaranteed". | ai.google.dev rate-limits, updated 2026-09-02 | No Gemini numbers are hard-coded anywhere. |
| Settings persist as one `AppSettings` value inside `.avg-meta.json` (saved on change by `scheduleMetaSave`), next to the projects folder in the app's Documents directory. No notification code exists in the app. | `Core/Store.swift` | New settings are plain fields, so they are saved for free. The saved chat is its own hidden file beside `.avg-meta.json` (Task 8). Notifications stay out of scope. |

**Decisions.**

- The chat is a **sheet** opened from a toolbar button on Library, Studio and Create. The native iOS 26 tab bar has five tabs; a sixth would collapse into "More".
- The settings are a new **Assistant** category on the Settings hub (own page, own search entries), not extra rows on Writing.
- `Store` owns `let agent = Agent()` so the UI still reaches everything through `store`.
- Projects are addressed by a **ref** (the last six characters of the project id), the full id, or a unique title fragment.
- After every finished message, that message's tool exchange is **compacted** away, keeping the user text and the final answer. This cuts tokens against Groq's 8K per minute. The assistant's own answer carries the refs it needs; it can call `list_projects` again.
- Prices come from one table (`CostRates`), shared by `Pipeline` (recorded costs) and the estimate on the approval card.

## File structure

| File | Responsibility |
|---|---|
| `Providers/AgentTypes.swift` (new) | `AgentToolSpec`, `AgentToolCall`, `AgentMessage`, `AgentProvider`, `AgentArgs`, `AgentHistory` |
| `Providers/AgentGroq.swift` (new) | Groq codec, `GroqAgent`, `AgentProviderFactory` |
| `Providers/AgentGemini.swift` (new, Task 11) | Gemini codec, `GeminiAgent` |
| `Providers/ErrorPolicy.swift` (modify) | `ErrorKind.malformedOutput` |
| `Core/AgentSettings.swift` (new) | `AgentProviderKind`, `AgentApprovalMode`, `AgentReplyStyle`, `AgentSettingsRules` |
| `Core/CostRates.swift` (new) | Rate table, `CostBreakdown`, `CostEstimate` |
| `Core/AgentTools.swift` (new) | `AgentHost`, the tools, dispatcher, approvals, refs, permission filtering |
| `Core/AgentPolicy.swift` (new) | `needsTap`, spend verdict, `AgentPrompt.system(settings:)` |
| `Core/Agent.swift` (new) | The observable loop, transcript, approval continuation, self-test, announcements, failover |
| `Core/AgentChatStore.swift` (new) | Save, load and delete the kept chat |
| `Core/Models.swift`, `Core/Store.swift`, `Core/Pipeline.swift` (modify) | Settings fields, `Store.agent`, finish hook, rates moved out of `Pipeline` |
| `UI/AgentView.swift` (new) | The chat sheet and the toolbar button |
| `UI/SettingsScreens.swift`, `UI/SettingsKit.swift`, `UI/SettingsView.swift`, `App.swift`, `UI/HomeView.swift`, `UI/StudioView.swift`, `UI/CreateView.swift` (modify) | Assistant page, category, anchors, hub row, sheet, toolbar button |
| `AVGStudioTests/Agent*Tests.swift`, `CostRatesTests.swift` (new) | Unit tests |

## Task protocol (replaces red/green)

CI is the only compiler. Each task: write the tests and the code, commit once, push to `feature/agent`, trigger the workflow with `workflow_dispatch` on that branch (the workflow supports it; whether this PAT may trigger it is **unverified**, otherwise fast-forward to `ios-native`, which triggers on push), and read `test.log` / `build.log` for `error:` and `XCTAssert`. A task is done when that run is green. Never say "passes" before it.

---

### Task 1: Agent vocabulary, tolerant arguments, history window and compaction

**Files:**
- Create: `AVGStudio/Providers/AgentTypes.swift`
- Test: `AVGStudioTests/AgentTypesTests.swift`

**Interfaces:**
- Produces:
  - `struct AgentToolParam: Sendable { let name: String; let kind: Kind; let summary: String; var required = true; var allowed: [String]? ; enum Kind { case string, integer, boolean } }`
  - `struct AgentToolSpec: Sendable { let name: String; let summary: String; let params: [AgentToolParam]; func jsonSchema() -> [String: Any] }`
  - `struct AgentToolCall: Sendable, Equatable { let id: String; let name: String; let argumentsJSON: String }`
  - `struct AgentMessage: Sendable, Equatable, Identifiable { enum Role { case user, assistant, tool }; let id: UUID; var role: Role; var text: String; var toolCalls: [AgentToolCall]; var toolCallID: String?; var toolName: String?; var providerPayload: Data? }` with `static func user(_:)`, `assistant(text:calls:payload:)`, `tool(callID:name:text:)`
  - `protocol AgentProvider: Sendable { func respond(system: String, messages: [AgentMessage], tools: [AgentToolSpec]) async throws -> AgentMessage }`
  - `struct AgentArgs { init(json: String); func string(_ k: String) -> String?; func int(_ k: String) -> Int?; func bool(_ k: String) -> Bool? }`
  - `enum AgentHistory { static func trimmed(_ m: [AgentMessage], keepLast: Int) -> [AgentMessage]; static func compacted(_ m: [AgentMessage], keepingLastTurn: Bool) -> [AgentMessage] }`

- [ ] **Step 1: Write the tests** in `AgentTypesTests`:
  - `testSchemaRendersTypesRequiredAndEnum`: a spec with `topic` (string, required), `aspect` (string, optional, allowed `["9:16","16:9","1:1"]`), `limit` (integer, optional) renders `type == "object"`, `properties.aspect.enum == ["9:16","16:9","1:1"]`, `properties.limit.type == "integer"`, `required == ["topic"]`.
  - `testArgsAreTolerant`: `{"limit":"3","flag":"true"}` gives `int("limit") == 3`, `bool("flag") == true`; `AgentArgs(json: "not json").string("x") == nil`; `AgentArgs(json: "[1,2]")` is empty; `{"topic":"  cats  "}` gives `"cats"`; `{"topic":"   "}` gives `nil`.
  - `testTrimNeverStartsOnAToolMessage`, `testTrimKeepsToolResultsWithTheirAssistantCall`, `testTrimLeavesShortHistoryUnchanged`: build `[user, assistant(calls), tool, assistant, user, assistant]` and trim with several `keepLast` values; every result begins with `.user`, and no `.tool` message appears without the assistant message that issued its `toolCallID` before it.
  - `testCompactionKeepsUserTextAndFinalAnswers`: for each completed turn it keeps the user message and the last assistant message that has no tool calls, and drops every `.tool` message and every assistant message with calls.
  - `testCompactionKeepsTheLastTurnIntactWhenAsked`: with `keepingLastTurn: true` an unfinished final turn keeps its tool exchange.
  - `testCompactionOfATurnWithNoFinalAnswerKeepsOnlyTheUserMessage`.
- [ ] **Step 2: Implement** the types above. `jsonSchema()` emits lower-case JSON-schema types (both Groq and the Gemini REST docs accept them). `AgentArgs` parses with `JSONSerialization`, accepts numbers or numeric strings for `int`, and `true/false` or `"true"/"false"` for `bool`. `trimmed`: cut to the last `keepLast` messages, move the cut forward to the first `.user` message at or after it; if there is none, cut back to the last `.user` message of the whole array. `compacted` splits into turns at each `.user` message.
- [ ] **Step 3: Self-check by reading:** no `print`, no UIKit import, all types `Sendable`.
- [ ] **Step 4: Commit** `feat(agent): provider-neutral tool/message types, tolerant args, history window, compaction`, then run the task protocol.

---

### Task 2: Assistant settings data

**Files:**
- Create: `AVGStudio/Core/AgentSettings.swift`
- Modify: `AVGStudio/Core/Models.swift` (`AppSettings` fields and `init(from:)`)
- Test: `AVGStudioTests/AgentSettingsTests.swift`, `AVGStudioTests/ModelsTests.swift`

**Interfaces:**
- Produces:
  - `enum AgentProviderKind: String, Codable, CaseIterable, Identifiable { case groq, gemini; var label: String }`
  - `enum AgentApprovalMode: String, Codable, CaseIterable, Identifiable { case always, paidOnly; var label: String }` (labels "Every video" and "Only videos that cost money")
  - `enum AgentReplyStyle: String, Codable, CaseIterable, Identifiable { case brief, normal; var label: String }`
  - `enum AgentSettingsRules { static let spendCapRange: ClosedRange<Double> = 0.25...10; static let spendCapStep = 0.25; static let spendCapDefault = 1.0; static let instructionsLimit = 400; static func spendCap(_ v: Double) -> Double; static func instructions(_ s: String) -> String }`
  - On `AppSettings`: `agentProvider = .groq`, `agentGroqModel = "llama-3.3-70b-versatile"`, `agentGeminiModel = "gemini-2.5-flash"`, `agentFailover = true`, `agentAllowCreate = true`, `agentAllowRedo = true`, `agentAllowCancel = true`, `agentApproval = .always`, `agentChatSpendCapUSD = 1.0`, `agentReplyStyle = .brief`, `agentAnnounceResults = true`, `agentKeepChat = false`, `agentInstructions = ""`

- [ ] **Step 1: Write the tests.**
  - `ModelsTests`: `testAgentSettingsDecodeToDefaultsFromEmptyJSON` (`{}` gives every default above); `testUnknownAgentEnumValuesFallBackToDefaults` (`agentProvider`, `agentApproval`, `agentReplyStyle` set to `"nope"`); `testAgentSettingsRoundTrip` (change every field, encode, decode, equal).
  - `AgentSettingsTests`: `testSpendCapClampsAndSnaps` (0 gives 0.25, 99 gives 10, 0.6 gives 0.5, `nan` gives 1.0); `testInstructionsAreTrimmedAndCapped` (leading and trailing whitespace removed, 500 characters become 400, emoji are counted as `Character`s so a cut never splits one); `testBlankInstructionsBecomeEmpty`.
- [ ] **Step 2: Implement** the enums, the rules and the 13 fields with 13 `c.value(...)` lines (skip a line and every saved setting silently resets, see commit `608f7e9`). An unknown enum raw value must fall back the same way the existing enum settings do; read how `c.value` handles that before writing the lines.
- [ ] **Step 3: Commit** `feat(settings): assistant settings data and rules`, then run the task protocol.

---

### Task 3: Groq backend and the malformed-call error kind

**Files:**
- Create: `AVGStudio/Providers/AgentGroq.swift`
- Modify: `AVGStudio/Providers/ErrorPolicy.swift`
- Test: `AVGStudioTests/AgentGroqTests.swift`, `AVGStudioTests/ErrorPolicyTests.swift`

**Interfaces:**
- Consumes: Task 1 types, Task 2 settings.
- Produces:
  - `ErrorKind.malformedOutput` with `POLICY` row `RetryRule(retryable: false, maxAttempts: 1, backoffSeconds: 0, onExhausted: .fail)`
  - `struct GroqAgent: AgentProvider { let key: String; let model: String }` with `static func encode(system: String, messages: [AgentMessage], tools: [AgentToolSpec], model: String) -> [String: Any]` and `static func decode(_ data: Data) throws -> AgentMessage`
  - `enum AgentProviderFactory { static func make(settings: AppSettings, kind: AgentProviderKind? = nil) throws -> any AgentProvider }` (`kind` overrides `settings.agentProvider`; Task 12 uses it)

- [ ] **Step 1: Write the tests.**
  - `ErrorPolicyTests`: add `.malformedOutput` to the `all` list in `testEveryKindHasAPolicyAndTheRulesAreCoherent`; `testGroqToolUseFailedIsMalformedOutput`: status 400, provider `"groq"`, body `{"error":{"code":"tool_use_failed","failed_generation":"…"}}` classifies as `.malformedOutput`, **including** when `failed_generation` contains the words "rate limit" and "harmful content" (the structural code must win over words inside model output).
  - `AgentGroqTests`: `testEncodeShape` (first message is `system`; assistant tool-call message encodes `tool_calls[0].function.arguments` as a string and `content` as null or empty; tool message carries `tool_call_id`; body has `tool_choice == "auto"`, `parallel_tool_calls == false`, `temperature == 0.2`, `max_completion_tokens == 4096`, no `max_tokens` key, `tools[0].function.parameters.type == "object"`); `testDecodeToolCalls` (literal JSON in Groq's documented shape yields one `AgentToolCall` with the same `id`); `testDecodePlainReply`; `testDecodeNullContentWithNoCallsThrows`; `testFinishReasonLengthWithoutCallsThrowsReadableError` (message mentions the reply was cut off).
- [ ] **Step 2: Implement** `ErrorKind.malformedOutput`: add the case, the `POLICY` row, `"groq": [.malformedOutput: ["tool_use_failed"]]` in `providerSpecific`, and put `.malformedOutput` **first** in `checkOrder`. Grep every `switch` over `ErrorKind` (Recovery has several); add the case where one is exhaustive.
- [ ] **Step 3: Implement** `GroqAgent` and the factory. `respond` posts to `https://api.groq.com/openai/v1/chat/completions` with header `Authorization: Bearer <key>` through `HTTP.retrying(…, provider: "groq")`. The factory uses `Keychain.require(.groq)` and `settings.agentGroqModel`, and throws `AVGError.message("The Gemini assistant arrives in a later update.")` for `.gemini` until Task 11.
- [ ] **Step 4: Commit** `feat(agent): Groq tool-calling backend, ErrorKind.malformedOutput`, then run the task protocol.

---

### Task 4: One cost table, and estimates for a new video and for a redo

**Files:**
- Create: `AVGStudio/Core/CostRates.swift`
- Modify: `AVGStudio/Core/Pipeline.swift` (`costText`, `costImage`, `costVoice`, `costTranscription`)
- Test: `AVGStudioTests/CostRatesTests.swift`

**Interfaces:**
- Produces: `enum CostRates { static func text(_ k: TextProviderKind) -> Double; static func image(_ k: ImageProviderKind) -> Double; static func voice(_ k: VoiceProviderKind) -> Double; static let transcriptionPerMinute: Double }` (values exactly as `Pipeline` has them today: Gemini text 0.00000025 and Groq 0.00000015 per character, Gemini image 0.02, OpenAI image 0.04, ElevenLabs 0.00003 and Gemini TTS 0.00002 per character, AssemblyAI 0.01 per minute; free providers 0). `struct CostBreakdown: Equatable { var text, images, voice, transcription: Double; func total(from phase: PhaseID) -> Double }`. `struct CostEstimate: Equatable { var usd: Double; var isFree: Bool { usd == 0 }; var summary: String }` with `static func forNewVideo(settings:channel:hasAssemblyKey:) -> CostEstimate` and `static func forRedo(from phase: PhaseID, settings:channel:hasAssemblyKey:) -> CostEstimate`.

- [ ] **Step 1: Write the tests.** `testDefaultsAreFree` (Apple/Pollinations/Apple gives `isFree` and summary `"free"`). `testElevenLabsMakesItPaid` (voice `.elevenlabs`, 60 s channel: voice cost is within 1% of `60 * 15 * 0.00003`). `testTextCostScalesWithLength` (120 s costs twice 60 s for Groq text alone). `testSummaryIsRounded` (`"≈ $0.03"` style, never more than two decimals; sub-cent paid work shows `"< $0.01"`, never `"free"`). `testRedoFromRenderIsFree` (render has no API cost). `testRedoFromVoiceExcludesTextAndImages`. `testRedoFromWritingEqualsNewVideo`. `testPaidSubCentIsNotFree` (a tiny non-zero cost still has `isFree == false`).
- [ ] **Step 2: Implement.** Estimate formula (a flat guess, said so in the doc comment like `CostEntry`): narration characters = `targetSeconds * 15`; text characters = 3 × narration; scenes = clamp(`targetSeconds / 8`, 5…12); image cost = scenes × image rate; voice cost = narration characters × voice rate; transcription only if `hasAssemblyKey`. Phase mapping for `total(from:)`: writing = text, visuals = images, voice = voice + transcription, captions = transcription, render = 0, and each phase includes every later phase. The `switch`es over provider kinds have no `default:`. Move the numbers out of `Pipeline` so `Pipeline` and the estimate read one table; recorded costs must not change.
- [ ] **Step 3: Commit** `refactor(costs): one rate table; add new-video and redo estimates`, then run the task protocol.

---

### Task 5: Host protocol, the tools, and permission filtering

**Files:**
- Create: `AVGStudio/Core/AgentTools.swift`
- Test: `AVGStudioTests/AgentToolsTests.swift`

**Interfaces:**
- Consumes: Tasks 1, 2, 4.
- Produces:
  - `@MainActor protocol AgentHost: AnyObject` requiring members **Store already has with these exact signatures**: `projects: [Project]`, `channels: [Channel]`, `activeChannel: Channel`, `settings: AppSettings`, `jobs: [String: JobState]`, `recoveryPlan: RecoveryPlan?`, `isRunning(_:) -> Bool`, `createProject(topic:title:channel:aspect:) -> Project`, `start(_:from:)`, `cancel(_:)`, `selectForStudio(_:)`, `record(_:_:error:)`
  - `struct AgentApproval: Identifiable, Equatable { let id: UUID; let title: String; let detail: String; let estimate: String; let estimateUSD: Double; let action: AgentApprovedAction }`
  - `enum AgentApprovedAction: Equatable { case createVideo(topic: String, title: String, aspect: AspectRatio, channelID: UUID); case redo(projectID: String, phase: PhaseID) }`
  - `enum AgentToolOutcome: Equatable { case result(String); case needsApproval(AgentApproval) }`
  - `struct AgentContext { var settings: AppSettings; var appleAvailable: Bool; var hasKey: (APIKey) -> Bool }`
  - `enum AgentTools { static func specs(for settings: AppSettings) -> [AgentToolSpec]; static func isGated(_ name: String) -> Bool; static func ref(_ p: Project) -> String; static func resolveProject(_ query: String, in projects: [Project]) -> Result<Project, AgentToolError>; static func run(_ call: AgentToolCall, host: any AgentHost, context: AgentContext) -> AgentToolOutcome; static func perform(_ action: AgentApprovedAction, host: any AgentHost) -> String }`

Tool contract (names and arguments are what the model sees; keep each `summary` to one short sentence to save tokens):

| Tool | Arguments | Gated | Offered when | Returns |
|---|---|---|---|---|
| `list_channels` | none | no | always | name, niche, aspect, target seconds, active flag |
| `list_projects` | `limit` (int, default 5, max 10) | no | always | newest first: ref, title, aspect, per-phase status, has video |
| `get_status` | `project` | no | always | phases, running phase and progress, "needs your decision" if `recoveryPlan?.projectID` matches, last error, total cost |
| `create_video` | `topic`, `title?`, `aspect?` (`9:16`/`16:9`/`1:1`), `channel?` (name) | **yes** | `agentAllowCreate` | an approval, never a started project |
| `redo_from` | `project`, `phase` (`writing`/`visuals`/`voice`/`captions`/`render`) | **yes** | `agentAllowRedo` | an approval |
| `cancel` | `project` | no | `agentAllowCancel` | "cancelled" or "not running" |
| `open_project` | `project` | no | always | switches Studio to it |

- [ ] **Step 1: Write the tests** with a `FakeHost` that records calls:
  - `testResolveByRefFullIDAndUniqueTitle`; `testAmbiguousTitleReturnsCandidatesNotAGuess` (error text contains both refs); `testUnknownProjectIsAnError`.
  - `testListProjectsDefaultsToFiveAndClampsToTen`.
  - `testGetStatusMentionsDecisionWhenRecoveryPending` and `testGetStatusShowsRunningPhase`.
  - `testCreateVideoNeedsApprovalAndTouchesNothing` (outcome is `.needsApproval`, `host.created` is empty, `estimate` non-empty, `estimateUSD` equals `CostEstimate.forNewVideo`); `testCreateVideoWithoutTopicIsAToolError`; `testUnknownChannelListsTheNames`; `testChannelDefaultsToActive`.
  - `testPreflightRefusesAppleWriterWhenUnavailable` (`appleAvailable: false`, `settings.text == .apple`: result mentions "Settings" and "Writing", no approval); `testPreflightRefusesMissingKey` (`text == .groq`, `hasKey` false: mentions the Groq key); `testPreflightPassesWithKey`.
  - `testRedoWhileRunningIsRefused`; `testRedoInvalidPhaseListsValidOnes`; `testRedoEstimateCoversOnlyFromThatPhase`.
  - `testSpecsOmitDisabledTools` (each of the three allow flags off removes exactly its tool; all off leaves four read-only tools); `testDisabledToolCalledAnywayIsRefusedWithTheSettingName` (the model calls `create_video` although it is not offered: result says it is turned off in Settings > Assistant, host untouched).
  - `testPerformCreateCallsCreateThenStartFromWriting` and `testPerformRedoStartsFromPhase`.
  - `testCancelIdleProjectSaysNotRunning`; `testUnknownToolIsAnErrorResultNotACrash`.
- [ ] **Step 2: Implement** `AgentTools`. `ref` is the last six characters of `Project.id`, matched case-insensitively. Tool results are short plain text (one line per item), never raw JSON, to keep the token cost low. The preflight runs inside `run` for `create_video` and `redo_from` before an approval is built. `open_project` calls `selectForStudio`.
- [ ] **Step 3: Commit** `feat(agent): AgentHost, the seven tools, permission filtering`, then run the task protocol.

---

### Task 6: The agent loop, self-test and `Store` wiring

**Files:**
- Create: `AVGStudio/Core/Agent.swift`
- Modify: `AVGStudio/Core/Store.swift` (add `let agent = Agent()`, set `agent.host = self` in `init()`, `extension Store: AgentHost {}`)
- Test: `AVGStudioTests/AgentLoopTests.swift`

**Interfaces:**
- Consumes: Tasks 1, 3, 5.
- Produces: `@MainActor @Observable final class Agent` with
  - `struct Line: Identifiable { enum Kind { case user, assistant, activity, error, announcement }; let id: UUID; let kind: Kind; let text: String }`
  - `private(set) var lines: [Line]`, `private(set) var isWorking: Bool`, `private(set) var pendingApproval: AgentApproval?`, `var isOpen: Bool`
  - `@ObservationIgnored weak var host: (any AgentHost)?`, `@ObservationIgnored var makeProvider: (AgentProviderKind?) throws -> any AgentProvider`
  - `@discardableResult func send(_ text: String) -> Task<Void, Never>`, `func approve()`, `func decline()`, `func stop()`, `func reset()`
  - `enum SelfTestResult: Equatable { case ok(String), failed(String) }` and `func selfTest() async -> SelfTestResult`
  - `static let maxSteps = 8`, `static let maxNudges = 2`, `static let historyWindow = 24`

Loop rules: one user turn runs at most `maxSteps` provider calls. Each call gets `AgentHistory.trimmed(wire, keepLast: historyWindow)`. A message with no tool calls ends the turn, after which the wire history is `AgentHistory.compacted(…, keepingLastTurn: false)`. Tool calls run in order; a gated outcome sets `pendingApproval` and suspends on a `CheckedContinuation`; its tool result is the performed action's text on approve, `"The user declined."` on decline. `ErrorKind.malformedOutput` appends a synthetic user message ("Your last tool call was malformed. Call the tool again with valid arguments, or answer in plain text.") and continues, at most `maxNudges` times per turn. Any other error becomes an `.error` line plus `host.record(nil, …, error: true)` and ends the turn. The whole turn runs inside `RenderLog.$record.withValue` bound to `host.record(nil, …)` so `HTTP.retrying` messages reach the Activity log. `send` is ignored while `isWorking`. This task's system prompt is a placeholder constant; Task 7 replaces it with `AgentPrompt.system(settings:)`.

`selfTest` sends one request: user text "Call the ping tool.", one `ping` tool with no arguments, through `makeProvider(nil)`. Result is `.ok("<provider label> · <model>")` only if the reply contains a call named `ping`; plain text gives `.failed("This model answered without using tools. Pick another model.")`; a provider error gives `.failed(<readable message>)`. It never touches the transcript, the wire history or the host.

- [ ] **Step 1: Write the tests** with a `ScriptedProvider` (queue of canned `AgentMessage`s, records what it was sent) and the `FakeHost` from Task 5:
  - `testPlainReplyEndsTheTurn`; `testToolCallResultIsSentBackWithMatchingID`.
  - `testStepCapStopsARunawayLoop` (provider always asks for a tool: exactly `maxSteps` calls, then an `.error` line, `isWorking == false`).
  - `testApprovalSuspendsThenApproveRunsTheActionOnce`; `testDeclineNeverTouchesTheHost`.
  - `testApproveTwiceStartsOnce` (second `approve()` is a no-op); `testStopWhileAwaitingApprovalResolvesAsDecline` (`pendingApproval == nil`, task finishes, host untouched).
  - `testMalformedOutputIsNudgedTwiceThenFails`; `testOtherErrorsBecomeAnErrorLineAndEndTheTurn`.
  - `testSendWhileWorkingIsIgnored`; `testResetClearsLinesAndWire`.
  - `testProviderSeesTrimmedHistory` (a long scripted history never reaches the provider starting on a `.tool` message).
  - `testFinishedTurnIsCompacted` (after a turn with two tool calls, the next provider call sees only user text and final answers from earlier turns).
  - `testSelfTestOkOnlyWhenPingIsCalled`, `testSelfTestPlainTextMeansModelCannotUseTools`, `testSelfTestErrorIsReadable`, `testSelfTestLeavesTranscriptUntouched`.
- [ ] **Step 2: Implement** `Agent`. Default `makeProvider` is `{ kind in try AgentProviderFactory.make(settings: self.host?.settings ?? AppSettings(), kind: kind) }`. Keep the wire history in an `@ObservationIgnored` array; `lines` is the UI transcript, with one `.activity` line per tool call (for example "Checking projects…"). Resume the approval continuation exactly once, guarded by a nil-check as `Store.resolveRecovery` does.
- [ ] **Step 3: Wire `Store`.** Add the property and the one-line conformance; `Store` already satisfies every `AgentHost` member, and the compiler will say if one signature differs.
- [ ] **Step 4: Commit** `feat(agent): bounded tool loop with approvals, compaction and self-test`, then run the task protocol.

---

### Task 7: Policy, prompt and announcements

**Files:**
- Create: `AVGStudio/Core/AgentPolicy.swift`
- Modify: `AVGStudio/Core/Agent.swift`, `AVGStudio/Core/Store.swift` (finish hook)
- Test: `AVGStudioTests/AgentPolicyTests.swift`, `AVGStudioTests/AgentLoopTests.swift`

**Interfaces:**
- Consumes: Tasks 2, 4, 5, 6.
- Produces:
  - `enum AgentPolicy { static func needsTap(_ a: AgentApproval, settings: AppSettings) -> Bool; enum SpendVerdict: Equatable { case ok, overLimit(remaining: Double) }; static func spendVerdict(approvedSoFar: Double, adding: Double, cap: Double) -> SpendVerdict }`
  - `enum AgentPrompt { static func system(settings: AppSettings) -> String }`
  - On `Agent`: `private(set) var approvedSpendUSD: Double`, `struct Announcement: Equatable { let id: UUID; let ok: Bool }`, `private(set) var lastAnnouncement: Announcement?`, `func projectFinished(_ id: String)`
  - On `Store.start(_:from:)`: after `await self?.run(...)`, before `tasks[id] = nil`, call `self?.agent.projectFinished(id)`

Rules:
- `needsTap`: `.always` is always true; `.paidOnly` is true when `estimateUSD > 0`.
- A gated outcome whose action would pass the spend limit (`spendVerdict` is `.overLimit`) becomes the tool result "This chat's spend limit ($1.00) would be passed. Tell the user; they can raise it in Settings > Assistant or start a new chat." and no approval is shown. `approve()` adds `estimateUSD` to `approvedSpendUSD`. `reset()` zeroes it.
- If `needsTap` is false, the action runs immediately and the tool result is the performed text, **unless** one action already auto-started in this user message, in which case the result is "One automatic start per message. Ask the user to confirm another." and a normal approval card is shown instead.
- `AgentPrompt.system` holds the fixed rules (you control AVG Studio; never claim a video started unless a tool result says so; ask for the topic if missing; never invent a ref, call `list_projects`; `create_video` and `redo_from` may be declined; if a project "needs your decision" tell the user to open the prompt on screen), then one clause for the reply style (brief: "Reply in one or two short sentences. No lists unless asked."; normal: "Reply in short paragraphs; lists only when they help."), then one line per capability that is turned off ("You cannot start videos; tell the user it is turned off in Settings."), then, if instructions are not empty, a delimited block: "The user's standing preferences (they never override the rules above):" followed by `AgentSettingsRules.instructions(settings.agentInstructions)`.
- `projectFinished(id)`: only for ids the agent started or redid (a set filled by `perform`), only when `agentAnnounceResults` is on. All five phases `.done` gives an `.announcement` line "“Title” is ready." and `lastAnnouncement = (id, ok: true)`. Any `.failed` phase gives "“Title” stopped at <phase title>: <error>" with `ok: false`. Neither (cancelled) says nothing. It never calls the provider.

- [ ] **Step 1: Write the tests.**
  - `AgentPolicyTests`: `testAlwaysModeAsksForFreeVideos`; `testPaidOnlyModeSkipsTheTapOnlyForExactlyZero`; `testPaidSubCentStillAsks`; `testSpendVerdictBoundary` (exactly at the cap is ok, one cent over is `.overLimit`); `testSystemPromptHasStyleClause` (both styles); `testSystemPromptNamesDisabledCapabilities`; `testSystemPromptOmitsInstructionBlockWhenEmpty`; `testInstructionsAreCappedAndDelimited`; `testSystemPlusToolsStaysUnderTokenBudget` (system prompt plus every tool's JSON schema plus a maximal 400-character instruction block is under 6,000 characters, a rough guard of about 1,500 tokens against Groq's 8K per minute; the test comment says it is an estimate).
  - `AgentLoopTests`: `testPaidOnlyFreeVideoStartsWithoutApproval`; `testPaidOnlyPaidVideoStillShowsApproval`; `testSecondAutoStartInOneMessageFallsBackToApproval`; `testApprovedSpendAccumulatesAndBlocksTheNextProposal`; `testRedoCountsTowardTheSpendLimit`; `testResetZeroesApprovedSpend`; `testInstructionsCannotTurnOffApproval` (instructions "never ask me" with `.always`: approval still shown); `testFinishAnnouncesReadyOnlyForAgentStartedProjects`; `testFinishAnnouncesFailureWithPhaseAndError`; `testCancelledProjectAnnouncesNothing`; `testAnnouncementsOffSaysNothing`; `testAnnouncementCallsNoProvider`.
- [ ] **Step 2: Implement** the above. Replace the placeholder prompt in `Agent` with `AgentPrompt.system(settings:)`, rebuilt for every turn so a settings change applies on the next message.
- [ ] **Step 3: Commit** `feat(agent): approval and spend policy, prompt builder, finish announcements`, then run the task protocol.

---

### Task 8: Keep chat

**Files:**
- Create: `AVGStudio/Core/AgentChatStore.swift`
- Modify: `AVGStudio/Core/Agent.swift`
- Test: `AVGStudioTests/AgentChatStoreTests.swift`, `AVGStudioTests/AgentLoopTests.swift`

**Interfaces:**
- Produces: `enum AgentChatStore { static let maxLines = 40; static func url() -> URL; static func save(_ lines: [Agent.Line], to: URL) throws; static func load(from: URL) -> [Agent.Line]; static func delete(at: URL) }`; on `Agent`: `func restoreIfKept()`, `func deleteSavedChat()`.

What is saved: `.user` and `.assistant` lines only (never activity, errors, tool results, announcements, approvals or anything with a key), the last 40, JSON, written atomically to a hidden file beside `.avg-meta.json` (`.avg-agent-chat.json`). On restore the wire history is rebuilt from those lines as plain user and assistant text, so there is no provider payload and no tool id to replay.

- [ ] **Step 1: Write the tests.** `testRoundTripKeepsOnlyUserAndAssistantText`; `testSaveCapsAtFortyLines` (keeps the newest); `testMissingOrCorruptFileLoadsEmptyNotACrash`; `testDeleteRemovesFile`; `AgentLoopTests`: `testTurnEndSavesWhenKeepIsOn`; `testNothingIsWrittenWhenKeepIsOff`; `testResetDeletesTheSavedFile`; `testRestoreRebuildsWireHistoryAsPlainText`; `testRestoreWithKeepOffDoesNothing`; `testSavedFileNeverContainsAnApprovalOrAKeyShapedString` (feed a transcript containing `gsk_…` as a user message and assert the saved file does not contain it: user text is the one thing that could, so the save step redacts any token matching the app's existing key-redaction helper; read how `ErrorLog` or `HTTP` redacts before writing this).
- [ ] **Step 2: Implement.** Save once at the end of each completed turn, not per token. `restoreIfKept()` is called from `Agent.host`'s `didSet` the first time a host is attached, only if `agentKeepChat` is on.
- [ ] **Step 3: Commit** `feat(agent): optional saved chat`, then run the task protocol.

---

### Task 9: The chat sheet

**Files:**
- Create: `AVGStudio/UI/AgentView.swift`
- Modify: `AVGStudio/App.swift` (`RootView`), `AVGStudio/UI/HomeView.swift`, `AVGStudio/UI/StudioView.swift`, `AVGStudio/UI/CreateView.swift`

**Interfaces:**
- Consumes: Tasks 6, 7, 8 (`store.agent`).
- Produces: `struct AgentView: View`, `struct AgentToolbarButton: View`.

- [ ] **Step 1: Build `AgentView`.** Transcript list with user bubbles, assistant bubbles, quiet `.activity` chips, `.announcement` rows with `Theme.ok`, and `.error` rows in `Theme.bad`; composer (text field, send, and Stop while `isWorking`); a header with "New chat" (`reset()`) and, when `approvedSpendUSD > 0`, a caption such as "Approved this chat: $0.04 of $1.00". Approval card shows `title`, `detail`, `estimate`, with **Approve as the screen's one `.glassProminent` button** and Decline as a plain button. If the chosen provider has no key (`Keychain.has`), show an empty state with a button that closes the sheet and goes to the Keys settings the way `Recovery.apply(.openKeys)` does; the empty state also says that messages go to Groq or Gemini. Radii and glass from `Theme` (card 24, inner 18). The typing indicator is a still dot under Reduce Motion.
- [ ] **Step 2: Present it.** In `RootView` add `.sheet(isPresented: $store.agent.isOpen) { AgentView() }` (if the compiler rejects binding through the `let agent`, use `Bindable(store.agent).isOpen`) and, so a failed render's `RecoverySheet` can show, `.onChange(of: store.recoveryPlan?.id) { if $1 != nil { store.agent.isOpen = false } }`. Add `.sensoryFeedback(.success, trigger:)` and `.sensoryFeedback(.error, trigger:)` keyed on `store.agent.lastAnnouncement?.id` and its `ok` flag (iOS 17+ API; no UIKit in `Core`). Add `AgentToolbarButton` to the toolbars of Library, Studio and Create (symbol `sparkle`, glass but not prominent, so Create's one filled button stays the only one). `open_project` must also close the sheet; do it in `Agent` after the tool runs.
- [ ] **Step 3: Self-check by reading:** grep the new UI for `.red`, `.green`, `.yellow`, `Picker`, `Toggle(`, `print(`; confirm only one `.glassProminent` per screen.
- [ ] **Step 4: Commit** `feat(agent): chat sheet and toolbar entry`, then run the task protocol. This is the first task whose result needs a device: run the checklist under "Definition of done" on the iPhone 12 from the unsigned IPA.

---

### Task 10: The Assistant settings page

**Files:**
- Modify: `AVGStudio/UI/SettingsKit.swift` (`SettingsCategory.assistant`, 14 `SettingAnchor`s, 14 `SettingsIndex` entries), `AVGStudio/UI/SettingsView.swift` (hub row and `page(_:focus:)` case), `AVGStudio/UI/SettingsScreens.swift` (`AssistantSettingsPage`)
- Test: `AVGStudioTests/SettingsIndexTests.swift` (existing tests cover the new anchors and category)

**Interfaces:**
- Consumes: Tasks 2, 6, 8.
- Produces: `SettingsCategory.assistant` (title "Assistant", symbol `sparkle`), anchors `assistantProvider, assistantModel, assistantTest, assistantFailover, assistantAllowCreate, assistantAllowRedo, assistantAllowCancel, assistantApproval, assistantSpendCap, assistantReplyStyle, assistantAnnounce, assistantKeepChat, assistantClear, assistantInstructions`, and `AssistantSettingsPage`.

Page layout, using the existing rows only (read `SettingsKit.swift` for a button-style row first; if there is none, add one small `ActionRow` beside `ToggleRow`):

| Group (footer) | Rows |
|---|---|
| **Model** ("Chats are sent to the service you pick. Models known to support tools: llama-3.3-70b-versatile, gpt-oss-120b, gpt-oss-20b.") | `ChoiceRow` provider; `ModelRow` for the selected provider (Groq or Gemini catalogue); **Test assistant** action with an inline result (green "Works: Groq · model" or the red message) |
| **Reliability** ("Separate from the writer's fallbacks. Needs a key for the other service.") | `ToggleRow` Switch assistant on limits |
| **What it may do** ("A turned-off action is not offered to the assistant at all.") | three `ToggleRow`s: start videos, redo steps, cancel |
| **Asking and spending** ("Videos that cost money always ask. The limit counts approved estimates, resets on New chat and on relaunch, and is a guard rail, not a bill.") | `ChoiceRow` Ask before starting; `StepperRow` Spend limit per chat (`AgentSettingsRules.spendCapRange`, step 0.25, shown as `$1.00`) |
| **Replies** | `SegmentRow` Reply length (Brief / Normal); `ToggleRow` Announce finished videos |
| **Memory** ("Saves only your messages and its answers, on this phone.") | `ToggleRow` Keep chat; **Clear saved chat** action (shown when Keep chat is on) |
| **Custom instructions** ("Sent with every message. They never turn off asking. Don't put keys here.") | `FieldRow` with the 400-character limit enforced through `AgentSettingsRules.instructions` and a live `n/400` caption |

Hub: add `row(.assistant, assistantSummary)` to the "Create" group, summary `"<provider label> · <model>"`.

- [ ] **Step 1: Add** the category (update the three exhaustive `switch`es: `title`, `symbol`, `page`), the 14 anchors, and the 14 index entries with unique titles: "Assistant provider", "Assistant model", "Test assistant", "Switch assistant on limits", "Assistant can start videos", "Assistant can redo steps", "Assistant can cancel", "Ask before starting", "Spend limit per chat", "Reply length", "Announce finished videos", "Keep chat", "Clear saved chat", "Custom instructions". Keywords include `assistant agent chat groq gemini` plus row-specific words (`approve confirm`, `budget cost money`, `memory history`, `prompt rules`).
- [ ] **Step 2: Build** `AssistantSettingsPage` per the table. Test assistant calls `store.agent.selfTest()` in a `Task` and disables itself while running. `onChange(of: store.settings.agentKeepChat)` turning off calls `store.agent.deleteSavedChat()`. Changing the provider clears the inline test result.
- [ ] **Step 3: Self-check by reading:** no native `Picker`, `Toggle`, `Stepper`; every row `.anchored(...)`; `SettingsIndexTests` would pass (each anchor has an entry, titles are unique, the category has an entry).
- [ ] **Step 4: Commit** `feat(settings): Assistant page`, then run the task protocol; check on device that searching "budget" lands on the spend limit.

---

### Task 11: Gemini backend

**Files:**
- Create: `AVGStudio/Providers/AgentGemini.swift`
- Modify: `AVGStudio/Providers/AgentGroq.swift` (`AgentProviderFactory`)
- Test: `AVGStudioTests/AgentGeminiTests.swift`

**Interfaces:**
- Consumes: Tasks 1, 2, 3.
- Produces: `struct GeminiAgent: AgentProvider { let key: String; let model: String }` with `static func encode(system:messages:tools:) -> [String: Any]` and `static func decode(_ data: Data) throws -> AgentMessage`.

- [ ] **Step 1: Write the tests.**
  - `testEncodeSendsSystemInstructionAndDeclarations` (`systemInstruction.parts[0].text`, `tools[0].functionDeclarations`, `toolConfig.functionCallingConfig.mode == "AUTO"`, `generationConfig.temperature == 0.2`).
  - `testModelTurnIsReplayedVerbatim` (an assistant message whose `providerPayload` holds a model `content` with a `thoughtSignature` encodes to a `contents` entry **equal to that stored JSON**, byte for byte on the signature).
  - `testToolResultsMergeIntoOneUserContentWithIDs` (two `.tool` messages produce one `role:"user"` content with two `functionResponse` parts carrying the call `id` and `name`).
  - `testDecodeIteratesPartsNotPositions` (text part before the `functionCall` part still yields the call).
  - `testDecodeSynthesisesIDWhenModelOmitsIt` (a 2.5-style call without `id` gets a stable id, and the matching `functionResponse` omits `id`).
  - `testMalformedFunctionCallFinishReasonThrowsMalformedOutput` (match case-insensitively; the docs spell it `Malformed_Function_Call`).
  - `testCompactedHistoryEncodesWithoutPayloads` (plain user and assistant text encodes cleanly with no `thoughtSignature` anywhere).
- [ ] **Step 2: Implement** `GeminiAgent`: POST to `https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent`, header `x-goog-api-key`, through `HTTP.retrying(…, provider: "gemini")`. Roles are `user` and `model`. Store the raw model `content` JSON in `providerPayload` on decode. Reuse `AgentToolSpec.jsonSchema()` for `parameters`. A `finishReason` of malformed call throws `AVGError.provider(.malformedOutput, provider: "gemini", message: …)`. Set the output ceiling to 4096 through `generationConfig`; I know the REST field as `maxOutputTokens` but did not re-verify it in this research pass, so check it against the function-calling page before relying on it.
- [ ] **Step 3: Replace** the `.gemini` stub in `AgentProviderFactory.make` with `GeminiAgent(key: try Keychain.require(.gemini), model: settings.agentGeminiModel)`.
- [ ] **Step 4: Commit** `feat(agent): Gemini function-calling backend`, then run the task protocol. Test on device with `gemini-2.5-flash` and once with `gemini-3.8-flash`, because only the second enforces thought signatures.

---

### Task 12: Failover between assistants

**Files:**
- Modify: `AVGStudio/Core/Agent.swift`, possibly `AVGStudio/Providers/HTTP.swift`
- Test: `AVGStudioTests/AgentLoopTests.swift`

**Interfaces:**
- Consumes: Tasks 3, 6, 11.
- Produces: no new public API. Behaviour: when `agentFailover` is on, the other provider has a key (`Keychain.has`), and a provider call fails with `ErrorKind` in `rateLimit, quota, billing, network, timeout` **before any tool call has happened in this user message**, the loop adds an activity line ("Groq is busy. Trying Gemini."), makes the other provider with `makeProvider(otherKind)`, and retries that same request once. The next user message starts on the primary provider again. After a tool call has run in the message, failover never happens; the error is shown as usual.

- [ ] **Step 1: Write the tests.** `testRateLimitBeforeAnyToolFailsOverOnce`; `testFailoverOffShowsTheError`; `testNoKeyForTheOtherProviderShowsTheError`; `testFailoverNeverHappensAfterAToolRan` (the first call returns `create_video`, the approval is approved, the second call fails with `rateLimit`: the host created exactly one project and the other provider was never made); `testNextMessageStartsOnThePrimaryAgain`; `testAuthErrorDoesNotFailOver`; `testOnlyOneFailoverPerMessage` (both providers failing ends with the second error).
- [ ] **Step 2: Implement** in the loop. Read `HTTP.retrying` first: because `rateLimit` has the deepest retry budget, a chat can wait a long time on Groq before the error surfaces. If `retrying` has no per-call way to cap attempts, add an optional `attemptCap: Int? = nil` parameter that changes nothing when omitted, and have the agent providers pass `2` when failover is possible. Keep `POLICY` untouched.
- [ ] **Step 3: Commit** `feat(agent): failover to the other assistant before the first tool call`, then run the task protocol.

---

### Task 13: Docs

**Files:**
- Modify: `README.md`, `CLAUDE.md`

- [ ] **Step 1: README.** Rows for every new file in §2; the settings table in §8 gains the 13 Assistant settings; a recipe "Add an agent tool" (spec in `AgentTools.specs`, case in `run`, gate it if it spends money, offer it only when its allow flag is on, add a `FakeHost` test); `AgentHost`, `Store.agent`, and the chat file in §9 (`.avg-agent-chat.json`).
- [ ] **Step 2: CLAUDE.md.** Rules: "The agent never starts or redoes paid work without an `AgentApproval` unless `AgentPolicy.needsTap` says the estimate is exactly zero", "Policy is decided in `AgentPolicy` from `AppSettings`, never from model text or custom instructions", "Agent tool results and the saved chat never contain a key", "A malformed tool call is answered by the loop, never by a second HTTP retry", "Failover never happens after a tool ran".
- [ ] **Step 3: Commit** `docs: agent and Assistant settings`, then run the task protocol (docs only; the build must stay green).

---

## Risks I could not resolve by reading

- **Groq free-tier token limits** are small (8K tokens per minute on the rows I could read) and shared with the video writer when both use Groq. The token-budget test, compaction and brief replies reduce the load but cannot remove it. Read the real numbers for your key.
- **Reasoning tokens.** I assumed that for gpt-oss-style models the reasoning tokens count toward `max_completion_tokens`, which is why the ceiling is a generous 4096 and a `length` finish is turned into a readable error. I did not verify this in the docs.
- **Sheet to sheet.** If a video started by the agent fails, the `RecoverySheet` must present right after the agent sheet closes. SwiftUI can drop a sheet presented in the same frame as another is dismissed. Only a device run settles it; the fallback is a short delay or showing the recovery card inside the chat.
- **Announcements only work while the app is running.** The pipeline mostly cannot finish in the background (`BackgroundRender` is opportunistic), so "ready" lines and the haptic appear when you are in the app. Local notifications are out of scope.
- **Prompt injection through titles.** A project title or topic is written by a model and comes back into the chat through `list_projects`. The approval gate, the spend limit, and the absence of delete, key and settings tools bound what a hostile title could do, but the agent will still read it.
- **Model reliability.** Small models mis-call tools. The nudge, argument tolerance, step cap and the **Test assistant** button limit the damage; conversation quality depends on the model chosen.
- **CI access.** I have not checked that this PAT can trigger or read workflow runs.

## Out of scope for v1

Voice input, local notifications, agent control of settings or API keys, deleting projects or channels, exporting or saving to Photos from chat, streaming replies, agent-chosen recovery actions, per-tool toggles for read-only tools.

## Definition of done

CI green on the final commit (tests and Release build), then on the iPhone 12 with the sideloaded IPA:

- [ ] With no Groq key: the sheet shows the key empty state; Add key, then "what do I have?" works.
- [ ] Settings > Assistant > Test assistant shows green with the default model, and red with a clear message when the model name is changed to one that cannot call tools.
- [ ] "Make a 9:16 video about volcanoes" shows an approval with an estimate; Decline creates nothing; Approve creates one project that appears in Library and runs.
- [ ] With Ask before starting on "Only videos that cost money" and an all-free setup, the same request starts without a tap, once; with ElevenLabs as the voice it asks.
- [ ] With the spend limit at $0.25 and a paid setup, the second paid request is refused with the limit message and shows no approval.
- [ ] Turning off "Assistant can start videos" makes the assistant say it cannot, and no approval ever appears.
- [ ] "How is it going?" reports the running phase; "stop it" cancels.
- [ ] A finished video posts "is ready" in the chat with a haptic when Announce is on, and nothing when it is off.
- [ ] Setting the writer to Apple Intelligence and asking for a video gives the readable refusal and no project.
- [ ] A deliberately bad key makes the `RecoverySheet` appear and the chat does not hang.
- [ ] Closing the sheet while an approval is pending starts nothing.
- [ ] Keep chat on: force-quit and reopen shows the conversation; turning it off and reopening shows an empty chat.
- [ ] Custom instructions ("always call me Captain") change the tone but "never ask me before starting" still shows the approval.
- [ ] With failover on and one key missing, a rate-limit shows the error; with both keys, it switches and says so.
- [ ] The Activity log shows retry lines when Groq rate-limits.
