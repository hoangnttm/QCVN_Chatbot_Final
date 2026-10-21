import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  {
    rules: {
      // React Compiler lint (eslint-plugin-react-hooks 7): it flags every "call a local async
      // loader from useEffect" data-fetching effect used across the app (chat, admin, applicability).
      // The compiler is not enabled in next.config.ts, so the rule is advisory here.
      "react-hooks/set-state-in-effect": "off",
    },
  },
]);

export default eslintConfig;
