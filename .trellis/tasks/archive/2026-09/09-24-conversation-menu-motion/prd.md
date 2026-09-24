# Conversation menu motion fix

- User reports clicking the conversation “⋮” shakes the page without showing its menu.
- Reproduced on the served app with `prefers-reduced-motion: reduce`: the global nonzero transition duration animates the popup's temporary measurement coordinates, producing alternating offscreen positions. A browser-only override to zero transition duration keeps all sampled menu frames in view.
- Scope: correct the reduced-motion CSS, add a real browser regression for menu visibility within the viewport and usable rename dialog, and rebuild/restart the existing local service.
- Acceptance: desktop and narrow navigation menus remain in view with reduced motion; normal rename/pin/archive/restore regression passes; production build and read-only browser verification pass.
- Preserve existing conversations, task evidence and unrelated files. No schema/API changes or dependency changes. Trellis controls this bounded fix; jj parent `13b29546` is the rollback anchor.
