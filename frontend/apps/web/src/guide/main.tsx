import { createRoot, hydrateRoot } from "react-dom/client";
import { GuideApp } from "./GuideApp";
import "../styles/tokens.css";
import "./guide.css";

const container = document.getElementById("guide-root")!;
// 生产环境容器内已有预渲染 HTML → hydrate 接管；开发环境为空 → 直接 render。
if (container.hasChildNodes()) {
  hydrateRoot(container, <GuideApp />);
} else {
  createRoot(container).render(<GuideApp />);
}
