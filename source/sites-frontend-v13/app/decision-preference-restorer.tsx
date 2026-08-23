"use client";

import { useEffect } from "react";

const STORAGE_KEY = "sa3arly-decision-preferences";

type Preferences = {
  mode?: string;
  tab?: string;
  filters?: Record<string, boolean>;
};

function load(): Preferences {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "{}") as Preferences;
  } catch {
    return {};
  }
}

function save(value: Preferences) {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
}

function filterKey(input: HTMLInputElement) {
  return input.closest("label")?.textContent?.replace(/\s+/g, " ").trim() || input.name || input.id;
}

function restore(root: ParentNode = document) {
  const preferences = load();
  const filterEntries = preferences.filters || {};
  root.querySelectorAll<HTMLInputElement>(".offer-filters input[type='checkbox']").forEach((input) => {
    const expected = filterEntries[filterKey(input)];
    if (typeof expected !== "boolean" || input.checked === expected) return;
    const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "checked");
    descriptor?.set?.call(input, expected);
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });

  if (preferences.mode) {
    const modeButton = [...root.querySelectorAll<HTMLButtonElement>(".comparison-mode-picker button")]
      .find((button) => button.textContent?.includes(preferences.mode || ""));
    if (modeButton && !modeButton.classList.contains("active")) modeButton.click();
  }
  if (preferences.tab) {
    const tabButton = [...root.querySelectorAll<HTMLButtonElement>(".comparison-tabs button")]
      .find((button) => button.textContent?.includes(preferences.tab || ""));
    if (tabButton && !tabButton.classList.contains("active")) tabButton.click();
  }
}

export default function DecisionPreferenceRestorer() {
  useEffect(() => {
    const onChange = (event: Event) => {
      const input = event.target;
      if (!(input instanceof HTMLInputElement) || !input.matches(".offer-filters input[type='checkbox']")) return;
      const current = load();
      save({
        ...current,
        filters: { ...(current.filters || {}), [filterKey(input)]: input.checked },
      });
    };
    const onClick = (event: MouseEvent) => {
      const button = event.target instanceof Element ? event.target.closest<HTMLButtonElement>("button") : null;
      if (!button) return;
      const current = load();
      if (button.closest(".comparison-mode-picker")) {
        save({ ...current, mode: button.querySelector("b")?.textContent?.trim() || button.textContent?.trim() });
      } else if (button.closest(".comparison-tabs")) {
        save({ ...current, tab: button.textContent?.replace(/\d+/g, "").trim() });
      }
    };
    const observer = new MutationObserver((mutations) => {
      if (mutations.some((mutation) => [...mutation.addedNodes].some((node) => node instanceof Element && (node.matches(".decision-center") || node.querySelector(".decision-center"))))) {
        queueMicrotask(() => restore());
      }
    });
    document.addEventListener("change", onChange);
    document.addEventListener("click", onClick);
    window.addEventListener("pageshow", () => restore());
    window.addEventListener("popstate", () => queueMicrotask(() => restore()));
    observer.observe(document.body, { childList: true, subtree: true });
    restore();
    return () => {
      document.removeEventListener("change", onChange);
      document.removeEventListener("click", onClick);
      observer.disconnect();
    };
  }, []);
  return null;
}
