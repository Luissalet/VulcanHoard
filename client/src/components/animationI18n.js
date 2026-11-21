// Match the browser language used by the library's viewer.
const english = (navigator.language || "es").toLowerCase().startsWith("en");

export const tr = (es, en) => english ? en : es;

export function manualSteps(report) {
  return tr(report.manual_es ?? report.manual, report.manual) ?? [];
}

export function profileText(profile) {
  if (profile.edited) return profile;
  if (profile.id === "web-animated") return {
    name: tr(profile.name, "Animated web"),
    description: tr(profile.description, "Web viewer with three.js or model-viewer: GLB with a lightweight skeleton, named clips, closed loops and 30 fps."),
  };
  if (profile.id === "game-runtime") return {
    name: tr(profile.name, "Game engine"),
    description: tr(profile.description, "Real-time character: requires idle, exact loops, no unused bones or empty channels, 4 influences per vertex and 30 fps."),
  };
  return profile;
}
