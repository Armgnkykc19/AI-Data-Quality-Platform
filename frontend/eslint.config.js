// Flat config. Deliberately small: the TypeScript rules and the React Hooks
// rules are the two sets that catch real defects in this codebase. No
// additional ESLint packages are declared, so nothing here depends on a
// transitive dependency of eslint itself.
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**', 'coverage/**'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [tseslint.configs.recommended],
  },
  {
    files: ['**/*.{ts,tsx}'],
    ...reactHooks.configs.flat['recommended-latest'],
  },
);
