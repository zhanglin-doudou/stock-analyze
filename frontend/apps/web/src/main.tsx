import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles/tokens.css";
import "./styles.css";
import { initPreferences } from "./theme/theme";

// Phase 0：只写 data-theme/data-density/data-trend，供 tokens.css 选择器消费；
// 旧 styles.css 尚未迁移，因此不改变现有视觉。
initPreferences();

const container = document.getElementById("root");
if (!container) throw new Error("找不到 #root 挂载点");
createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
