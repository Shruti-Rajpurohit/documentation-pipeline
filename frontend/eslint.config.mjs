import js from "@eslint/js";
import { globalIgnores } from "eslint/config";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

const eslintConfig = [
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
  js.configs.recommended,
  ...tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
];

export default eslintConfig;