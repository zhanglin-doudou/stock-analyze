import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

/**
 * Phase 4 · ESLint flat config（web）。
 * 重点规则：零口径 no-restricted-syntax（唯一豁免 charts/geometry.ts 与测试）。
 * 说明：零口径门禁同时由 `src/charts/zero-caliber.test.ts` 在 vitest 中兜底，
 * 防止有人绕过 ESLint 直接改测试/构建配置。
 */
const ZERO_CALIBER = [
  {
    selector: "CallExpression[callee.name='parseFloat']",
    message: "零口径：业务代码禁止 parseFloat；数值解析仅允许 src/charts/geometry.ts。",
  },
  {
    selector: "CallExpression[callee.name='parseInt']",
    message: "零口径：业务代码禁止 parseInt；数值解析仅允许 src/charts/geometry.ts。",
  },
  {
    selector: "CallExpression[callee.name='Number']",
    message: "零口径：业务代码禁止 Number()；数值解析仅允许 src/charts/geometry.ts。",
  },
  {
    selector: "CallExpression[callee.property.name='toFixed']",
    message: "零口径：业务代码禁止 toFixed；格式化由后端返回，前端不得补零/四舍五入。",
  },
];

export default [
  { ignores: ["dist/**", "node_modules/**", "**/*.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "no-undef": "off",
      // 中文 UI 使用全角空格做视觉间隔（JSX 文本），不视为错误。
      "no-irregular-whitespace": [
        "error",
        { skipStrings: true, skipTemplates: true, skipComments: true, skipJSXText: true },
      ],
      "no-restricted-syntax": ["error", ...ZERO_CALIBER],
    },
  },
  {
    files: ["src/charts/geometry.ts", "src/**/*.test.{ts,tsx}"],
    rules: { "no-restricted-syntax": "off", "require-yield": "off" },
  },
];
