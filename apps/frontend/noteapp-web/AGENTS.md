# Design System: NoteAI

Follow these guidelines for all future UI additions and modifications.

## Style & Vibe
- **Vibe:** Modern-Tech, intellectual, secure, focused power-user tool.
- **Theme:** Deep oceanic dark mode.

## Color Palette
- **Backgrounds:**
  - Main app background: `#1a1a2e`
  - Sidebar / Panel background: `#16213e`
  - Card / Surface background: `#121316`
  - Active / Hover background: `#1f2b4a`
- **Borders & Subtle Accents:** `#0f3460` or `#43474f`
- **Primary Highlight:** `#e94560` (Vibrant pinkish-red) used for call-to-actions, active states, and AI glow effects.
- **Secondary Highlight:** `#a9c8fc` (Soft blue)

## Typography
- **UI & Headings:** `Inter`
- **Editor & Code Elements:** `JetBrains Mono`
- **Text colors:**
  - Headings / Primary text: `#e3e2e6`
  - Secondary text / Subtitles: `slate-400` or `slate-500`

## Components & Layout
- **Shadows:** Use dark, tinted shadows (`shadow-black/20` up to `shadow-black/50`) to create depth.
- **Borders:** Use 1px borders of `#0f3460` across cards, sidebars, and top navs.
- **AI Elements:** Add left borders `border-l-2 border-[#e94560]` and subtle background gradients (`from-white/10 to-transparent`) for AI insights or active elements.
- **Icons:** Use `lucide-react`.

## Navigation & Architecture
- Maintain the persistent left sidebar with the NoteAI branding and navigation items.
- Maintain the top bar with search and quick actions.
- Ensure the main content area has `overflow-y-auto` while the layout shell is `h-screen overflow-hidden`.
