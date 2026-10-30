import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
// 旧 styles.css 先加载，tokens 后加载以覆盖旧的 :root 变量（A+ 浅色/深色同一套语义变量）。
import "./styles.css";
import "./styles/tokens.css";
import "./styles/dark.css";
import { initPreferences } from "./theme/theme";

// 启动即应用主题/密度/涨跌色偏好；tokens.css 负责视觉映射。
initPreferences();

const container = document.getElementById("root");
if (!container) throw new Error("找不到 #root 挂载点");
createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
