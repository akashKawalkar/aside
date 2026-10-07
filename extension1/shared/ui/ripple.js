// shared/ui/ripple.js — Material press ripple via one delegated listener.
// Any element with the .stateful class gets a hover/focus state layer from
// CSS and a ripple on press from here.

let installed = false;

export function installRipple(root = document) {
  if (installed) return;
  installed = true;

  root.addEventListener(
    "pointerdown",
    (event) => {
      if (event.button !== 0) return;

      const target = event.target instanceof Element ? event.target.closest(".stateful") : null;
      if (!target || target.matches(":disabled, [aria-disabled='true']")) return;

      const rect = target.getBoundingClientRect();
      const diameter = Math.hypot(rect.width, rect.height) * 2;
      const isIconButton = target.classList.contains("icon-btn") || target.classList.contains("check");

      const x = isIconButton ? rect.width / 2 : event.clientX - rect.left;
      const y = isIconButton ? rect.height / 2 : event.clientY - rect.top;

      const ripple = document.createElement("span");
      ripple.className = "ripple";
      ripple.style.width = ripple.style.height = `${diameter}px`;
      ripple.style.left = `${x - diameter / 2}px`;
      ripple.style.top = `${y - diameter / 2}px`;

      target.appendChild(ripple);
      ripple.addEventListener("animationend", () => ripple.remove(), { once: true });
      setTimeout(() => ripple.remove(), 900); // in case animations are disabled
    },
    { passive: true }
  );
}
