/**
 * jsdom polyfill（Radix primitives 依赖）。
 * 只补缺失 API，不改变既有测试行为。
 */
if (typeof window !== "undefined") {
  if (!("ResizeObserver" in window)) {
    class ResizeObserverStub {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
    Object.defineProperty(window, "ResizeObserver", { value: ResizeObserverStub });
  }

  if (!("PointerEvent" in window)) {
    Object.defineProperty(window, "PointerEvent", { value: MouseEvent });
  }

  const proto = window.HTMLElement.prototype as unknown as Record<string, unknown>;
  if (typeof proto.scrollIntoView !== "function") proto.scrollIntoView = () => {};
  if (typeof proto.setPointerCapture !== "function") proto.setPointerCapture = () => {};
  if (typeof proto.releasePointerCapture !== "function") proto.releasePointerCapture = () => {};
  if (typeof proto.hasPointerCapture !== "function") proto.hasPointerCapture = () => false;
  if (typeof window.scrollTo !== "function") window.scrollTo = (() => {}) as typeof window.scrollTo;
}
