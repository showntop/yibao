# Capability Surface v2 Design QA

## Evidence

- Source visual truth: `/private/tmp/yibao-capability-audit/08-final-qa.png`
- Browser-rendered implementation: `/private/tmp/yibao-capability-audit/13-formal-final.jpg`
- Responsive evidence: `/private/tmp/yibao-capability-audit/15-formal-compact.jpg`
- Full-view comparison: `/private/tmp/yibao-capability-audit/14-formal-before-after.jpg`
- Focused rail and command-bar comparison: `/private/tmp/yibao-capability-audit/16-formal-focused.jpg`
- Desktop source and implementation: 1280 x 720 CSS px and 1280 x 720 image px.
- Compact implementation: 900 x 720 CSS px and 900 x 720 image px.
- Browser device pixel ratio: 1. No density resampling was required.
- State: capability surface open, board view active, no selected object, 6 topics, 10 mixed timeline entries, shared command bar idle.

The source and implementation intentionally contain different task data. The source establishes the approved spatial model; the implementation exercises the formal mixed-timeline and restore behavior with one additional topic and later activity.

## Findings

No actionable P0, P1, or P2 findings remain.

- Fonts and typography: the implementation retains Yibao's existing system font stack, weight hierarchy, line height, truncation, and small-label treatment. The denser activity summaries remain readable and use stronger text only for the action result.
- Spacing and layout rhythm: the system rail, collaboration timeline, primary stage, and shared command bar preserve the approved hierarchy. Mixed timeline entries add density without pushing or compressing the stage controls.
- Colors and visual tokens: the existing neutral surfaces, blue context tint, green success state, borders, shadows, and selected tokens are reused. The new filters and activity states do not introduce a competing visual language.
- Image and icon fidelity: existing Yibao avatar, logo, and icon components are reused. No product imagery or icon was replaced with placeholder or CSS-drawn art.
- Copy and content: the rail now explains that conversation, plugin calls, and results remain in one task timeline. Activity summaries name the capability, action, object scope, state, and time without exposing raw tool output.
- Interaction states: board/list, filters, activity expansion, object selection, command send, new topic, surface close/restore, focus mode, Escape hierarchy, and refresh restore were exercised.
- Responsive behavior: at 900 px the collaboration timeline yields completely, while the stage and shared command bar remain visible with no horizontal overflow.
- Accessibility scope checked: semantic regions and buttons are present, focus mode removes the hidden conversation from the accessibility tree, and Escape restores it. Screenshot review cannot establish full screen-reader or WCAG compliance.

The focused comparison covers the two regions where added density could have changed the design: the collaboration rail and shared command bar. No additional focused crop was required for the board because its components and token usage are unchanged from the approved source.

## Primary Browser Checks

1. Open and restore the capability surface from the task timeline.
2. Filter the timeline between all entries, conversation, and capability activity.
3. Expand an activity to reveal its bounded detail.
4. Select a topic and verify it becomes the command-bar object scope.
5. Send `基于这个再找 5 个案例` and verify user message, activity, assistant reply, and source count update.
6. Refresh and verify surface, selected object, board/list view, topics, draft, and timeline restore without re-running an action.
7. Create a topic, close the surface, and restore the same work context.
8. Verify Escape clears object scope, exits focus, then closes the surface.
9. Verify the 900 px reflow and absence of horizontal overflow.
10. Browser console warnings and errors: none.

## Comparison History

1. The approved source established the spatial model: system rail + collaboration context + primary stage + shared command bar.
2. Formal v2 added a mixed task timeline, filters, expandable capability activities, durable prototype state, board/list state, object scope, and surface restore.
3. First v2 review found a P1: timeline activity items could flex-shrink into unreadable horizontal slivers. Fixed by preventing timeline items from shrinking; post-fix evidence showed every entry at its natural height.
4. The same review found a P2: every activity exposed a restore action when the surface was closed. Fixed so only the newest relevant activity offers `恢复工作面`.
5. The same review found a P2: sending a command while the timeline was filtered to `活动` hid the new conversation result. Fixed by returning the filter to `全部` on open/send.
6. Post-fix desktop evidence is `/private/tmp/yibao-capability-audit/13-formal-final.jpg`; compact evidence is `/private/tmp/yibao-capability-audit/15-formal-compact.jpg`. No P0/P1/P2 findings remain.

## Follow-up Boundary

- Prototype persistence is versioned localStorage for interaction validation only. Production must move the task timeline to a task/session-scoped sidecar store and must restore state without replaying plugin actions.

final result: passed

## Production Integration QA

- Desktop evidence: `/private/tmp/yibao-capability-production-desktop.png`
- Compact evidence: `/private/tmp/yibao-capability-production-compact.png`
- Entry: `home.html?qa=capability` in Vite dev only. The fixture reuses the production Home, HomePlugins, SchemaPanel, shared command bar, capability rail, and responsive CSS; it substitutes only deterministic IPC data. `import.meta.env.DEV` removes the fixture branch from production builds.
- Production frontend build: passed (`vue-tsc --noEmit && vite build`).
- Rust bridge check: passed (`cargo check`).
- Sidecar regression: 60 passed (`test_history.py`, `test_plugins.py`, `test_ipc.py`).
- Real Tauri boot: app and sidecar started; agents, coding, forge, notes, reminders, toolbox, and zimeiti plugins reported ready.

Verified interactions:

1. A capability Stage uses the full remaining task width, with a 286–310 px mixed collaboration rail rather than an overlay panel.
2. Focus mode removes the collaboration rail and gives the plugin the full scene while retaining the same shared command bar.
3. Escape exits Focus, then collapses Stage; the top activity capsule remains available and restores the same panel without replaying an action.
4. Conversation/activity filtering and bounded activity detail remain interactive.
5. At 900 × 720 the rail yields completely, the stage width is 900 px, and `scrollWidth === clientWidth` (no horizontal overflow).
6. The isolated QA page produced no console warnings or errors.
7. Hidden model-produced panels report no object focus until the user expands them; persisted history projection strips raw tool arguments before data reaches the WebView.

production integration result: passed
