import js from '@eslint/js'
import { defineConfig } from 'eslint/config'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'
import tseslint from 'typescript-eslint'

// One linter, no second formatter. Ruff owns the Python side and this owns the
// browser side; adding a formatter that disagrees with the linter is how a
// repository grows two opinions about a blank line.
export default defineConfig(
  { ignores: ['dist', 'coverage', 'playwright-report', 'test-results', 'src/api/schema.ts'] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // `any` is not a fallback. The API types are generated from the backend
      // schema, so a value without a type here means the schema was not asked.
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/restrict-template-expressions': [
        'error',
        { allowNumber: true, allowBoolean: true },
      ],
      // Disabled because the rule itself crashes on this file set
      // (`typeParameters.params is not iterable`, typescript-eslint 8.46-8.66).
      // It is a style rule about merging overloads; nothing here declares an
      // overload, so switching it off costs no coverage. Revisit when the
      // upstream defect is fixed.
      '@typescript-eslint/unified-signatures': 'off',
    },
  },
  {
    // This file configures the linter and is not application code. Two of its
    // imports ship no types, so the type-aware rules can only report that they
    // cannot see them -- which says nothing about the browser bundle.
    files: ['eslint.config.js'],
    rules: {
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
    },
  },
  {
    // Build and test tooling: plain JavaScript that reads JSON off disk and
    // shells out. Type-aware rules can only report that JSON is untyped, which
    // is true and says nothing. The rules that catch real mistakes -- unused
    // bindings, unreachable code, undefined names -- still apply.
    files: ['scripts/**/*.mjs'],
    extends: [tseslint.configs.disableTypeChecked],
    rules: {
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-call': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
      '@typescript-eslint/no-unsafe-argument': 'off',
      '@typescript-eslint/no-unsafe-return': 'off',
      '@typescript-eslint/restrict-plus-operands': 'off',
    },
  },
)
