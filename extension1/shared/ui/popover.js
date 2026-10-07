// shared/ui/popover.js — anchored popovers for menus and pickers.
// Opens below the anchor (flips above when there is no room), clamps to the
// viewport, closes on outside press or Escape, and restores focus.

import { h } from "./dom.js";

const MARGIN = 8;
let current = null;

export function closePopover() {
  current?.close();
}

/**
 * openPopover({ anchor, content, className, align, onClose })
 *  - content: an element to show
 *  - align: "start" | "end" — which anchor edge the popover lines up with
 * Returns { el, close, reposition }.
 */
export function openPopover({ anchor, content, className = "", align = "start", onClose } = {}) {
  closePopover();

  const el = h("div", { class: `popover ${className}`.trim(), role: "dialog" }, content);
  el.style.visibility = "hidden";
  document.body.appendChild(el);

  let closed = false;

  const reposition = () => {
    const a = anchor.getBoundingClientRect();
    const { offsetWidth: w, offsetHeight: height } = el;
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;

    let left = align === "end" ? a.right - w : a.left;
    left = Math.max(MARGIN, Math.min(left, vw - w - MARGIN));

    const spaceBelow = vh - a.bottom - MARGIN;
    const spaceAbove = a.top - MARGIN;
    const above = height > spaceBelow && spaceAbove > spaceBelow;

    let top = above ? a.top - height - 4 : a.bottom + 4;
    top = Math.max(MARGIN, Math.min(top, vh - height - MARGIN));

    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
    el.style.setProperty("--origin-x", `${Math.max(0, Math.min(a.left + a.width / 2 - left, w))}px`);
    el.style.setProperty("--origin-y", above ? "100%" : "0");
  };

  reposition();
  el.style.visibility = "visible";

  const close = () => {
    if (closed) return;
    closed = true;
    document.removeEventListener("pointerdown", onPointerDown, true);
    document.removeEventListener("keydown", onKeyDown, true);
    window.removeEventListener("resize", reposition);
    if (current?.el === el) current = null;

    el.classList.add("closing");
    el.addEventListener("animationend", () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 300);

    onClose?.();
    if (anchor.isConnected && document.activeElement === document.body) anchor.focus?.();
  };

  const onPointerDown = (event) => {
    if (el.contains(event.target) || anchor.contains(event.target)) return;
    close();
  };

  const onKeyDown = (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
    }
  };

  // Registered on the next tick so the click that opened us does not close us.
  setTimeout(() => {
    if (closed) return;
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
  }, 0);
  window.addEventListener("resize", reposition);

  current = { el, close };
  return { el, close, reposition };
}
