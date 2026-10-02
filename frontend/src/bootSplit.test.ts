// Issue #149: App.tsx statically imported every page, so the build emitted ONE eager chunk —
// 1,001 kB raw / 291 kB gzip — that every visitor downloaded and executed before anything
// rendered, including the no-login /todo/{token} PWA that is opened dozens of times a day on
// a phone.
//
// The fix is structural, and structure is the only thing that can protect it: a RENDER test
// cannot see this regression at all. `crm/gtd/PublicTodoApp.test.tsx` boots the real Root and
// would keep passing, green and unchanged, if someone turned either lazy() back into a static
// import and put the CRM back in front of every /todo visitor. That is what this file is for.
//
// THE CHUNKS, because the assertions below mean different things for each:
//   - the ENTRY chunk — whatever `main.tsx` reaches eagerly (Root, BootFallback,
//     ChunkErrorBoundary, publicMode). Downloaded by EVERY visitor, /todo included.
//   - the PUBLIC TODO chunks — `PublicTodoApp` + the shared `crm/gtd/pages` module and its
//     transitive graph. Downloaded by a /todo visitor and by a CRM visitor's first GTD page.
//   - the SHELL chunk — whatever `App.tsx` reaches eagerly. Downloaded by every CRM visitor,
//     the login page included.
//   - the ROUTE chunks — one per lazy page (plus the assistant drawer), downloaded on demand.
// A static page import in `App.tsx` inflates the SHELL (bad, but /todo is unaffected). A static
// import in `main.tsx` or a non-lazy branch in `Root.tsx` inflates the ENTRY chunk, which is
// the original 1 MB failure in miniature. A CRM module reachable from `PublicTodoApp` inflates
// the PUBLIC download — the thing this issue exists to shrink — and no per-file allowlist can
// see that, so the last describe walks the real transitive graph.
//
// It reads SOURCE rather than inspecting a build because it has to run in the normal
// `npm test` sweep, where no `dist/` exists — and because the invariant IS about the import
// statements. Source comes in through `import.meta.glob(…, { query: '?raw' })`: `node:fs` is
// unavailable to a `src/**` test under tsconfig.app.json's `types: ["vite/client"]`, and
// widening that was rejected in vitest.config.ts. (The empty-string trap documented there is
// CSS-specific; `?raw` on .ts/.tsx returns the real text.)
import * as ts from 'typescript';
import { describe, expect, it } from 'vitest';

/** Every non-test source module as text, keyed by its path relative to src/ ('./App.tsx'). */
const SOURCES = import.meta.glob(['./**/*.{ts,tsx}', '!./**/*.test.*'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

function read(rel: string): string {
  const src = SOURCES[rel];
  if (src === undefined) throw new Error(`no source module at ${rel} — moved or renamed?`);
  return src;
}

/** Compare specifiers without their extension — `./Root` and `./Root.tsx` are the same
 *  module, and pinning the literal spelling would fail a correct refactor for no reason. */
const bare = (spec: string) => spec.replace(/\.tsx?$/, '');

function parse(source: string): ts.SourceFile {
  return ts.createSourceFile('probe.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
}

/**
 * Every module this source pulls in EAGERLY, via the TypeScript parser rather than a regex.
 *
 * The AST is not fussiness — a regex misses real shapes (a wrapped import, a double-quoted
 * path, an `export … from` re-export, a bare side-effect `import './App'` that binds nothing
 * but still drags the whole graph in, two statements on one line, the whitespace-free
 * `import{default as App}from'./App'`). `the scanner recognises …` below pins each of them.
 *
 * `import(…)` inside `lazy()` is an EXPRESSION, not an ImportDeclaration, so it is invisible
 * here by construction. That is exactly right: the dynamic form is what we want, and a
 * scanner that counted it would make every assertion below vacuous.
 *
 * `import type` / `export type` are skipped — they are erased at build time and carry no
 * runtime weight, so banning them would fail a file for no payload cost.
 */
function staticImportSpecifiers(source: string): string[] {
  const specifiers: string[] = [];
  for (const statement of parse(source).statements) {
    if (ts.isImportDeclaration(statement)) {
      if (statement.importClause?.isTypeOnly) continue;
    } else if (ts.isExportDeclaration(statement)) {
      if (statement.isTypeOnly) continue;
    } else {
      continue;
    }
    const spec = statement.moduleSpecifier;
    if (spec && ts.isStringLiteral(spec)) specifiers.push(spec.text);
  }
  return specifiers;
}

/**
 * Every `import('…')` call in the source, anywhere in the tree (the lazy() arguments).
 *
 * `isStringLiteralLike`, not `isStringLiteral`, and a `<computed>` entry for everything else —
 * both because this scanner FAILS CLOSED or it is worthless. Recording only plain string
 * literals meant a backtick import(`../../crm/CrmLayout`) produced no entry at all, so the
 * public-graph assertion (`dynamic` must be empty) passed while that import downloaded the CRM
 * on /todo. An import whose target cannot be read statically is exactly the case a guard must
 * refuse, not the case it may skip.
 */
function dynamicImportSpecifiers(source: string): string[] {
  const specifiers: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const arg = node.arguments[0];
      if (arg && ts.isStringLiteralLike(arg)) specifiers.push(arg.text);
      else specifiers.push('<computed>');
    }
    ts.forEachChild(node, visit);
  };
  visit(parse(source));
  return specifiers;
}

/**
 * Resolve a relative specifier the way the bundler does, against the SOURCES key space.
 * Throws on a miss: "can't tell" must FAIL, not silently drop a module from the sweep.
 */
function resolve(importer: string, spec: string): string {
  const parts = importer.split('/').slice(0, -1);
  for (const seg of spec.split('/')) {
    if (seg === '..') parts.pop();
    else if (seg !== '.') parts.push(seg);
  }
  const base = parts.join('/');
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`]) {
    if (candidate in SOURCES) return candidate;
  }
  throw new Error(`cannot resolve '${spec}' from ${importer}`);
}

/**
 * The transitive STATIC graph from one module: every source module the bundler puts in the
 * same download as the root, plus every bare package specifier met on the way. Dynamic
 * imports are deliberately not followed — that is the boundary this test exists to defend.
 */
function staticClosure(root: string): {
  modules: Set<string>;
  packages: Set<string>;
  dynamic: Array<[string, string]>;
  globs: string[];
} {
  const modules = new Set<string>();
  const packages = new Set<string>();
  // Every dynamic import found INSIDE the closure, as [importer, specifier]. Not followed —
  // the dynamic edge is the boundary under test — but reported, because a `void
  // import('../../CrmLayout')` in a GTD page downloads the CRM the moment /todo mounts, and
  // following-nothing-and-reporting-nothing would call that graph clean.
  const dynamic: Array<[string, string]> = [];
  // `import.meta.glob` is the other way in, and the more dangerous one: with `eager: true` it
  // bundles every match STATICALLY, so a single glob in a reached module can pull a directory
  // into the download while no ImportDeclaration mentions any of it.
  const globs: string[] = [];
  const stack = [root];
  while (stack.length) {
    const key = stack.pop()!;
    if (modules.has(key)) continue;
    modules.add(key);
    const source = read(key);
    for (const spec of staticImportSpecifiers(source)) {
      if (spec.endsWith('.css')) continue; // a stylesheet, not JS weight
      if (spec.startsWith('.')) stack.push(resolve(key, spec));
      else packages.add(spec.startsWith('@') ? spec.split('/').slice(0, 2).join('/') : spec.split('/')[0]);
    }
    for (const spec of dynamicImportSpecifiers(source)) dynamic.push([key, spec]);
    for (const pattern of importMetaGlobPatterns(source)) globs.push(`${key}: ${pattern}`);
  }
  return { modules, packages, dynamic, globs };
}

/**
 * Every `import.meta.glob(...)` call in the source, reported by its first argument.
 *
 * Matched structurally (a call whose callee is a property access on `import.meta`) rather than
 * by text, so `import.meta.glob` written across a line break or aliased through a local const is
 * still seen. The first argument may be a string or an array of them; anything else is reported
 * as `<computed>` so a glob built at runtime is never silently invisible.
 */
function importMetaGlobPatterns(source: string): string[] {
  const file = parse(source);

  // Aliases, resolved the way `lazyCallSites` resolves `lazy` — and for the same measured
  // reason. `const g = import.meta.glob; const m = g('../components/*.tsx', {eager:true})`
  // bundles a whole directory statically, and a callee-shape-only matcher records nothing for
  // it, so `expect(globs).toEqual([])` passes while the CRM rides into the /todo download.
  const aliases = new Set<string>();
  const collectAliases = (node: ts.Node) => {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer &&
      ts.isPropertyAccessExpression(node.initializer) &&
      node.initializer.name.text === 'glob' &&
      ts.isMetaProperty(node.initializer.expression)
    ) {
      aliases.add(node.name.text);
    }
    ts.forEachChild(node, collectAliases);
  };
  collectAliases(file);

  const patterns: string[] = [];
  const visit = (node: ts.Node) => {
    const isGlobCall =
      ts.isCallExpression(node) &&
      ((ts.isPropertyAccessExpression(node.expression) &&
        node.expression.name.text === 'glob' &&
        ts.isMetaProperty(node.expression.expression)) ||
        (ts.isIdentifier(node.expression) && aliases.has(node.expression.text)));
    if (ts.isCallExpression(node) && isGlobCall) {
      const arg = node.arguments[0];
      if (arg && ts.isStringLiteralLike(arg)) patterns.push(arg.text);
      else if (arg && ts.isArrayLiteralExpression(arg)) {
        patterns.push(arg.elements.map((e) => (ts.isStringLiteralLike(e) ? e.text : '<computed>')).join(' , '));
      } else patterns.push('<computed>');
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return patterns;
}

/**
 * Every call to React's `lazy`, with whether it sits inside a function.
 *
 * Resolves the LOCAL NAME `lazy` is imported under (`import { lazy as makeLazy } from 'react'`
 * is the same call), so this cannot be dodged by renaming, and cannot be satisfied by the word
 * appearing in a comment. A lazy() evaluated inside a component body mints a new component type
 * on every render, remounting the whole subtree beneath it — the route content on each render
 * for `App`, and the entire chat for `AssistantLauncher`.
 */
function lazyCallSites(source: string): Array<{ insideFunction: boolean }> {
  const file = parse(source);

  // Which local identifier(s) refer to react's `lazy`? Three shapes, because each is a real
  // way to write it and a scanner that knew only the first was MEASURED to be evadable: a
  // module-scope `const mkLazy = lazy` plus `mkLazy(...)` inside the component body is a true
  // remount regression that an import-name-only detector reported as clean.
  const names = new Set<string>();
  const reactNamespaces = new Set<string>();
  for (const statement of file.statements) {
    if (!ts.isImportDeclaration(statement)) continue;
    if (!ts.isStringLiteral(statement.moduleSpecifier) || statement.moduleSpecifier.text !== 'react') continue;
    const clause = statement.importClause;
    if (!clause) continue;
    // 1. `import { lazy }` / `import { lazy as makeLazy }`
    const bindings = clause.namedBindings;
    if (bindings && ts.isNamedImports(bindings)) {
      for (const element of bindings.elements) {
        if ((element.propertyName ?? element.name).text === 'lazy') names.add(element.name.text);
      }
    }
    // 2. `import React from 'react'` / `import * as React from 'react'` → `React.lazy(...)`
    if (clause.name) reactNamespaces.add(clause.name.text);
    if (bindings && ts.isNamespaceImport(bindings)) reactNamespaces.add(bindings.name.text);
  }

  // 3. Re-aliasing, to a fixpoint: `const a = lazy; const b = a;`
  //
  // Scanned over the WHOLE tree, not just `file.statements`. A module-scope-only sweep left the
  // guard failing open through the shortest possible evasion — the alias declared inside the
  // component next to the call it enables:
  //     function App() { const mk = lazy; const P = mk(() => import('./P')); … }
  // …which is the exact remount bug this guard exists to catch, and it stayed green.
  const collectAliases = (node: ts.Node) => {
    if (ts.isVariableDeclaration(node) && node.initializer) {
      const init = node.initializer;
      if (ts.isIdentifier(node.name)) {
        // `const mk = lazy` / `const mk = React.lazy`
        if (ts.isIdentifier(init) && names.has(init.text)) names.add(node.name.text);
        if (
          ts.isPropertyAccessExpression(init) &&
          init.name.text === 'lazy' &&
          ts.isIdentifier(init.expression) &&
          reactNamespaces.has(init.expression.text)
        ) {
          names.add(node.name.text);
        }
      } else if (ts.isObjectBindingPattern(node.name) && ts.isIdentifier(init) && reactNamespaces.has(init.text)) {
        // `const { lazy } = React` / `const { lazy: mk } = React` — the destructured form, which
        // an identifier-only check skips entirely, leaving the binding unknown and every call
        // through it invisible to the module-scope rule.
        for (const element of node.name.elements) {
          const sourceName = element.propertyName ?? element.name;
          if (ts.isIdentifier(sourceName) && sourceName.text === 'lazy' && ts.isIdentifier(element.name)) {
            names.add(element.name.text);
          }
        }
      }
    }
    ts.forEachChild(node, collectAliases);
  };
  for (let pass = 0; pass < 5; pass += 1) {
    const before = names.size;
    collectAliases(file);
    if (names.size === before) break;
  }

  const isLazyCallee = (expr: ts.Expression): boolean =>
    (ts.isIdentifier(expr) && names.has(expr.text)) ||
    (ts.isPropertyAccessExpression(expr) &&
      expr.name.text === 'lazy' &&
      ts.isIdentifier(expr.expression) &&
      reactNamespaces.has(expr.expression.text));

  /** Is this function the callback handed to a `lazy(...)` call? */
  const isLazyArgument = (node: ts.Node): boolean => {
    const parent = node.parent;
    return !!parent && ts.isCallExpression(parent) && isLazyCallee(parent.expression) && parent.arguments[0] === node;
  };

  const sites: Array<{ insideFunction: boolean }> = [];
  const visit = (node: ts.Node, insideFunction: boolean) => {
    if (ts.isCallExpression(node) && isLazyCallee(node.expression)) {
      sites.push({ insideFunction });
    }
    // The lazy() ARGUMENT is itself an arrow function, so descending into a call we just
    // recorded must not flip the flag — only a function that ENCLOSES the call counts.
    const opensScope =
      ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) ||
      ts.isArrowFunction(node) || ts.isMethodDeclaration(node);
    const nested = insideFunction || (opensScope && !isLazyArgument(node));
    ts.forEachChild(node, (child) => visit(child, nested));
  };
  visit(file, false);
  return sites;
}

/**
 * Is a JSX element named `descendant` nested INSIDE one named `ancestor`?
 *
 * Source-position ordering ("`<ChunkErrorBoundary>` appears before `<Suspense`") is NOT this
 * question, and the difference is the whole point: two SIBLINGS satisfy the ordering while the
 * boundary catches nothing the Suspense throws. A rejected chunk would then escape and blank
 * `#root` — the exact failure the boundary exists to prevent — with the guard still green.
 */
function jsxNests(source: string, ancestor: string, descendant: string): boolean {
  const nameOf = (node: ts.Node): string | null => {
    if (ts.isJsxElement(node)) return node.openingElement.tagName.getText();
    if (ts.isJsxSelfClosingElement(node)) return node.tagName.getText();
    return null;
  };
  let found = false;
  const search = (node: ts.Node, insideAncestor: boolean) => {
    if (found) return;
    const name = nameOf(node);
    const nowInside = insideAncestor || name === ancestor;
    if (insideAncestor && name === descendant) { found = true; return; }
    ts.forEachChild(node, (child) => search(child, nowInside));
  };
  search(parse(source), false);
  return found;
}

const count = (haystack: string, needle: string) => haystack.split(needle).length - 1;

describe('boot split (#149) — the entry chunk', () => {
  it('main.tsx imports nothing but React, stylesheets and Root', () => {
    // An ALLOWLIST, not a ban on `./App`: main.tsx is the entry module, so anything it
    // imports is eager for every visitor on every surface. Naming `./App` alone would let a
    // static `PublicTodoApp` — or any page — in through the side door while this stayed green.
    // Stylesheets (index.css, the @fontsource faces) are allowed by extension: CSS carries no
    // JS graph, and that is the whole reason a CSS import cannot re-merge anything.
    const allowed = new Set(['react', 'react-dom/client', './Root']);
    const specs = staticImportSpecifiers(read('./main.tsx')).map(bare);
    expect(specs).toContain('./Root');
    expect(specs.filter((s) => !s.endsWith('.css') && !allowed.has(s))).toEqual([]);
  });

  it('Root.tsx lazy-loads BOTH branches of the public/CRM dispatch', () => {
    const src = read('./Root.tsx');

    // Root is reached eagerly from main.tsx, so its static imports are ENTRY-chunk weight —
    // paid by every visitor. Allowlisted for the same reason main.tsx is.
    const allowed = new Set([
      'react',
      './core/components/BootFallback',
      './core/components/ChunkErrorBoundary',
      './crm/gtd/publicMode',
    ]);
    const specs = staticImportSpecifiers(src).map(bare);
    expect(specs.filter((s) => !allowed.has(s))).toEqual([]);

    // Either branch going static would defeat the split: a static App ships the CRM to a
    // /todo visitor, a static PublicTodoApp ships the todo app to every CRM visitor.
    expect(dynamicImportSpecifiers(src).map(bare).sort()).toEqual(['./App', './crm/gtd/PublicTodoApp']);
    expect(src).toMatch(/lazy\(\s*\(\)\s*=>\s*import\('\.\/App'\)/);
    expect(src).toMatch(/lazy\(\s*\(\)\s*=>\s*\n?\s*import\('\.\/crm\/gtd\/PublicTodoApp'\)/);

    // The boundary wraps the Suspense, not the other way round: a chunk that fails to load
    // rejects INSIDE the Suspense, and only an ancestor boundary can catch it.
    // ANCESTRY, not source order. Two siblings would satisfy an index comparison while the
    // boundary caught nothing the Suspense threw — a rejected chunk would escape it and blank
    // the page, which is the one failure this whole composition exists to prevent.
    expect(jsxNests(src, 'ChunkErrorBoundary', 'Suspense'), 'Suspense nests inside ChunkErrorBoundary').toBe(true);
    expect(count(src, '<ChunkErrorBoundary>')).toBe(1);
    expect(count(src, '<Suspense')).toBe(1);
    expect(src.indexOf('<ChunkErrorBoundary>')).toBeLessThan(src.indexOf('<Suspense'));
  });

  it('the entry chunk reaches no page, no CRM module and no heavy package', () => {
    // The transitive proof for the file-level allowlists above: whatever main.tsx reaches
    // statically is downloaded by a /todo visitor before the todo app is even requested.
    const { modules, packages, dynamic, globs } = staticClosure('./main.tsx');
    expect(modules).toContain('./Root.tsx');
    expect(modules).toContain('./crm/gtd/publicMode.ts');
    expect(modules.size).toBeLessThanOrEqual(5);
    expect([...packages].sort()).toEqual(['react', 'react-dom']);

    // Root's two branches are the ONLY dynamic imports the entry graph may hold. A third would
    // be a chunk every visitor's boot path could fetch without any static import naming it.
    expect(dynamic.map(([, spec]) => bare(spec)).sort()).toEqual(['./App', './crm/gtd/PublicTodoApp']);
    expect(globs, 'no import.meta.glob in the entry graph').toEqual([]);
  });
});

describe('boot split (#149) — the CRM shell', () => {
  it('App.tsx imports no page module statically', () => {
    const src = read('./App.tsx');

    // The shell every CRM surface needs anyway. Listing them states the intended shape
    // instead of banning a directory outright, so a reviewer can see what was meant to be
    // eager and why — and adding to this list is a deliberate act, not an accident.
    //
    // EVERY ENTRY IS A LEAF MODULE, NEVER A BARREL — that is the invariant, not a preference.
    // An allowlisted barrel is a hole this guard cannot see through: `export { default as
    // SomePage } from './SomePage'` added to it re-inflates the shell with every assertion
    // here still green. `./assistant` is the proof that shape is realistic in this repo (it
    // re-exports AssistantPanelBody, the heaviest module in the app), and `./shared/dnd`,
    // `./shared/search`, `./shared/collection`, `./shared/listview` are barrels too.
    const eagerAllowed = new Set([
      'react',
      'react-router-dom',
      './core/auth/AuthContext',
      './core/auth/ProtectedRoute',
      './core/branding/BrandingContext',
      './core/components/BootFallback',
      './login/LoginPage',
      './crm/gtd/TodosModeRouter',
      // #169's /crm/tasks -> /crm/todos redirect. A leaf that imports react-router-dom
      // and nothing else, so it pulls no page graph into the shell chunk.
      './crm/legacyTodoRedirect',
      './shared/ToastViewport',
      './shared/ConfirmHost',
    ]);
    const specs = staticImportSpecifiers(src).map(bare);
    expect(specs.filter((s) => !eagerAllowed.has(s))).toEqual([]);

    // Named explicitly as well as excluded by the allowlist, so a future edit that widens the
    // list still trips over the barrels that actually re-export heavy modules.
    for (const barrel of ['./assistant', './shared/dnd', './shared/collection', './shared/search', './shared/listview']) {
      expect(specs, barrel).not.toContain(barrel);
    }

    // Sanity: the conversion actually happened and stayed converted — nine CRM pages plus the
    // ten GTD routes. A route added as a lazy() raises this; one added statically fails above.
    expect(dynamicImportSpecifiers(src).length).toBeGreaterThanOrEqual(19);
  });

  it('every GTD route in App.tsx goes through the one shared pages module', () => {
    // Ten per-page dynamic imports would give the phone PWA ten chunk requests on every cold
    // load: PublicTodoApp shares those modules, so the bundler would split each page out of
    // the todo download into its own chunk. One module, one chunk, for both mounts.
    const gtdDynamic = dynamicImportSpecifiers(read('./App.tsx')).filter((s) => s.startsWith('./crm/gtd/'));
    expect(gtdDynamic.length).toBe(10);
    expect(new Set(gtdDynamic)).toEqual(new Set(['./crm/gtd/pages']));
  });

  it('the toast and confirm hosts stay OUTSIDE the route Suspense boundary', () => {
    // A toast in flight or an open confirm dialog must never be replaced by a loading state
    // while a route chunk downloads. Purely positional — nothing but ordering in the JSX
    // enforces it, so nothing but this notices if it moves.
    const src = read('./App.tsx');

    // Source ORDER, not a JSX-ancestry proof. What makes ordering sufficient is uniqueness:
    // with exactly one Suspense in the file, "the host appears after the boundary closes" and
    // "the host is outside it" coincide.
    expect(count(src, '<Suspense')).toBe(1);
    expect(count(src, '</Suspense>')).toBe(1);
    expect(count(src, '<ConfirmHost />')).toBe(1);
    expect(count(src, '<ToastViewport />')).toBe(1);

    const suspenseOpen = src.indexOf('<Suspense');
    const suspenseClose = src.indexOf('</Suspense>');
    expect(suspenseOpen).toBeLessThan(src.indexOf('<Routes>'));
    expect(src.indexOf('</Routes>')).toBeLessThan(suspenseClose);
    expect(suspenseClose).toBeLessThan(src.indexOf('<ConfirmHost />'));
    expect(suspenseClose).toBeLessThan(src.indexOf('<ToastViewport />'));
  });

  it('TodosModeRouter keeps TodosPage lazy — it is imported eagerly by the shell', () => {
    const src = read('./crm/gtd/TodosModeRouter.tsx');
    const specs = staticImportSpecifiers(src).map(bare);
    expect(specs.filter((s) => !new Set(['react', 'react-router-dom', './TodoModeContext']).has(s))).toEqual([]);
    // TodosPage drags the collection layer and @dnd-kit; static here = in the shell chunk.
    expect(dynamicImportSpecifiers(src).map(bare)).toEqual(['../TodosPage']);
  });

  it('CrmLayout wraps its Outlet in a Suspense boundary', () => {
    // The route chunks load INSIDE the chrome: the nav stays put, and the layout's own fetches
    // run in parallel with the download instead of after it. Also the boundary that catches
    // the todo mode flipping from unknown to GTD — a plain setState, not a router transition,
    // so without this the whole layout would be replaced by the fallback.
    const src = read('./crm/CrmLayout.tsx');
    expect(count(src, '<Suspense')).toBe(1);
    expect(count(src, '<Outlet />')).toBe(1);
    const open = src.indexOf('<Suspense');
    const close = src.indexOf('</Suspense>');
    expect(open).toBeLessThan(src.indexOf('<Outlet />'));
    expect(src.indexOf('<Outlet />')).toBeLessThan(close);

    // "The Outlet is inside it" is only HALF the invariant, and the weaker half. Hoisting the
    // boundary to wrap the entire layout body — nav, logo, bell, launcher, with the Outlet
    // still nested somewhere inside — satisfies every assertion above while turning each route
    // chunk load and each todo-mode flip into a full-shell spinner, which is precisely the
    // behaviour this boundary was added to PREVENT. (Measured: that mutation kept all 803
    // frontend tests green before these two lines existed.) So pin what must stay OUTSIDE.
    // Stated as "not BETWEEN the tags" rather than "before the open tag", because outside is
    // outside in either direction and pinning the current source order would fail a correct
    // rearrangement for no reason.
    // `AssistantLauncher` belongs on this list as much as the nav does: a boundary hoisted to
    // swallow the launcher and the content — but not the nav — would pass a nav-only check
    // while hiding the assistant every time a route chunk or the todo mode is in flight.
    const navMarkers = ['NAV_ITEMS.map', 'Sign out', '<AssistantLauncher'];
    expect(navMarkers.length).toBe(3);
    for (const marker of navMarkers) {
      const occurrences: number[] = [];
      for (let at = src.indexOf(marker); at !== -1; at = src.indexOf(marker, at + 1)) occurrences.push(at);
      expect(occurrences.length, `CrmLayout still renders ${marker}`).toBeGreaterThan(0);
      const swallowed = occurrences.filter((at) => at > open && at < close);
      expect(swallowed, `${marker} renders OUTSIDE the route Suspense, never inside it`).toEqual([]);
    }

    // And the route content has a boundary of its own, for the same reason the drawer does:
    // after a deploy every route chunk 404s at once, and a rejection walks past Suspense. Route
    // scope, not panel — the user IS blocked on the page they asked for, so the one-shot
    // deploy-skew reload still applies; it is only drawn smaller so the nav survives.
    expect(jsxNests(src, 'ChunkErrorBoundary', 'Outlet'),
      'the Outlet sits inside a ChunkErrorBoundary').toBe(true);
    expect(count(src, '<ChunkErrorBoundary scope="route"'),
      'the route boundary is route-scoped').toBe(1);
    // …and it is RESET on navigation. A React error boundary never self-resets, and this one
    // sits outside the Outlet, so unwired it would hold its card across every later navigation:
    // one render bug freezing the content column for the whole session while the nav around it
    // keeps working. The prop is the fix; passing it is what makes the fix real.
    expect(src, 'the route boundary is reset on navigation').toMatch(/resetKey=\{location\.pathname\}/);
  });

  it('the assistant drawer loads its chat surface lazily, by leaf path', () => {
    // The heaviest graph in the app (react-markdown + highlight.js) rides CrmLayout's chunk
    // otherwise, i.e. every authenticated page load — with zero AI keys included.
    const src = read('./crm/components/AssistantLauncher.tsx');
    const specs = staticImportSpecifiers(src).map(bare);
    expect(specs).not.toContain('../../assistant');
    expect(specs).not.toContain('../../assistant/AssistantPanelBody');
    expect(dynamicImportSpecifiers(src).map(bare)).toEqual(['../../assistant/AssistantPanelBody']);
    // Mounted whenever AI is ready, not on first open — that is the "stays mounted so chat
    // state survives" contract, and it also means the chunk arrives before the first open.
    // Both indices must EXIST before they are compared. `indexOf` returns -1 for a missing
    // needle, and -1 is less than every real position — so the ordering assertion alone passes
    // most loudly in exactly the case it is meant to catch: the `ready` gate deleted, and a
    // keyless install downloading 347 kB it can never use.
    const gate = src.indexOf('{ready && (');
    const panel = src.indexOf('<AssistantPanelBody');
    expect(gate, 'AssistantLauncher still gates the drawer on `ready`').toBeGreaterThan(-1);
    expect(panel, 'AssistantLauncher still renders AssistantPanelBody').toBeGreaterThan(-1);
    expect(gate).toBeLessThan(panel);

    // The containment, pinned. Suspense catches a PENDING chunk and never a REJECTED one, so
    // without a boundary of its own a drawer chunk that 404s after a deploy takes the whole CRM
    // down — over a panel nobody opened. Measured before this assertion existed: deleting the
    // boundary, AND downgrading it to the app scope (which would auto-RELOAD over that panel,
    // destroying the work the containment protects), both kept all 810 tests green.
    expect(jsxNests(src, 'ChunkErrorBoundary', 'AssistantPanelBody'),
      'the drawer body sits inside a ChunkErrorBoundary').toBe(true);
    expect(count(src, '<ChunkErrorBoundary scope="panel">'),
      'the drawer boundary is panel-scoped, so it never reloads the page').toBe(1);
  });

  it('every lazy() is declared at module scope, never inside the component', () => {
    // A lazy() evaluated inside the component would mint a new component identity on every
    // render, remounting the whole route subtree (or, in AssistantLauncher, wiping the chat)
    // — the never-declare-a-component-inside-a-component rule, in the shape these files could
    // plausibly regress into.
    const bodies: Array<[string, string]> = [
      ['./App.tsx', 'export default function App()'],
      ['./Root.tsx', 'export default function Root()'],
      ['./crm/gtd/TodosModeRouter.tsx', 'export function TodosModeRouter('],
      ['./crm/components/AssistantLauncher.tsx', 'export function AssistantLauncher('],
    ];
    expect(bodies.length).toBe(4);
    for (const [file, marker] of bodies) {
      const src = read(file);
      expect(src.indexOf(marker), `${file} has ${marker}`).toBeGreaterThan(-1);
      // AST, not a text split on the component marker. A `/lazy\(/` regex over the source
      // BELOW the marker answers a different question in both directions: a comment mentioning
      // lazy() fails a correct file, and `const L = makeLazy(...)` — or any alias — passes a
      // broken one. `lazyCallSites` resolves the actual `lazy` binding imported from react and
      // reports whether each call sits under a function.
      const sites = lazyCallSites(src);
      expect(sites.length, `${file} calls lazy()`).toBeGreaterThan(0);
      const nested = sites.filter((s) => s.insideFunction);
      expect(nested, `${file} declares every lazy() at module scope`).toEqual([]);
    }
  });
});

describe('boot split (#149) — the public todo download', () => {
  const GTD_PAGES = [
    './DonePage', './InboxPage', './NextActionsPage', './ProjectDetailPage', './ProjectsPage',
    './ReviewPage', './SearchPage', './SomedayPage', './TodayPage', './WaitingPage',
  ];

  it('crm/gtd/pages.ts re-exports exactly the ten GTD pages and nothing else', () => {
    // This module IS the public surface's download (and App's shared GTD chunk). A CRM page
    // re-exported from here would ship the CRM to every /todo visitor.
    const src = read('./crm/gtd/pages.ts');
    const statements = parse(src).statements;
    expect(statements.length).toBe(10);
    expect(statements.every((s) => ts.isExportDeclaration(s) && !s.isTypeOnly)).toBe(true);
    expect(staticImportSpecifiers(src).map(bare).sort()).toEqual([...GTD_PAGES].sort());
  });

  it('PublicTodoApp imports its pages through that one module', () => {
    const specs = staticImportSpecifiers(read('./crm/gtd/PublicTodoApp.tsx')).map(bare);
    expect(specs.sort()).toEqual(['../../shared/ToastViewport', './pages', './publicMode', 'react-router-dom']);
  });

  it('nothing reachable from PublicTodoApp is a CRM module or a heavy package', () => {
    // The transitive contract, walked over the real source graph. File-level allowlists
    // cannot see a `crm/gtd/components/RecordChip` that one day imports `crm/components/…`
    // and drags the CRM into the phone PWA's download; this does.
    const { modules, packages, dynamic, globs } = staticClosure('./crm/gtd/PublicTodoApp.tsx');

    // Coverage first, unconditionally, so the deny-list loop below can never pass on an
    // empty sweep (vitest's `expect.requireAssertions` catches a zero-assertion test, not a
    // sweep that quietly examined nothing).
    expect(modules.size).toBeGreaterThanOrEqual(40);
    for (const sentinel of ['./crm/gtd/pages.ts', './crm/gtd/TodoShell.tsx', './crm/gtd/api.ts', './shared/search/index.ts']) {
      expect(modules, sentinel).toContain(sentinel);
    }

    // Everything the surface reaches lives in one of these places. `core/api/client` is the
    // one known passenger: gtd/api.ts imports it for the authenticated branch and never calls
    // it in public mode — measured at 1.1 kB raw, not worth a second client module.
    const allowedPrefixes = ['./crm/gtd/', './shared/'];
    const allowedLeaves = new Set(['./core/api/client.ts', './core/auth/tokenUtils.ts']);
    const stray = [...modules].filter(
      (m) => !allowedLeaves.has(m) && !allowedPrefixes.some((p) => m.startsWith(p)),
    );
    expect(stray).toEqual([]);

    // The shared collection layer IS in this download since #234, deliberately: Someday, Done
    // and Projects render through `CollectionView`, and the layer reaches @dnd-kit through
    // KanbanView. Measured on the built /todo cold load (entry chunk + PublicTodoApp + every
    // static import): 320.6 → 417.0 kB raw, 101.4 → 132.2 kB gzip. Pinned as a sentinel so the
    // cost stays a known, recorded fact rather than an accident this sweep would miss if the
    // pages moved off the layer again (and so the package list below keeps its meaning).
    expect(modules).toContain('./shared/collection/CollectionView.tsx');

    expect([...packages].sort()).toEqual([
      '@dnd-kit/core', '@dnd-kit/sortable', '@dnd-kit/utilities', 'react', 'react-router-dom',
    ]);

    // The two ways CRM code can enter this download without any ImportDeclaration naming it,
    // both of which the static walk above would call clean:
    //   • a dynamic `void import('../../CrmLayout')` — a separate chunk, still fetched the
    //     moment /todo mounts, so the phone downloads the CRM anyway;
    //   • `import.meta.glob(..., { eager: true })` — bundled STATICALLY, a whole directory
    //     pulled in with no specifier to allowlist against.
    // Neither exists today, and both must stay a deliberate act rather than an accident.
    expect(dynamic, 'no dynamic import inside the public todo graph').toEqual([]);
    expect(globs, 'no import.meta.glob inside the public todo graph').toEqual([]);
  });
});

describe('boot split (#149) — the scanner itself', () => {
  it('recognises every eager-import shape, and no dynamic one', () => {
    // Pins the guard. Every entry here is a real way to bundle a page eagerly, and a regex
    // predecessor of this scanner (upstream) missed each one in turn.
    const eagerShapes: Array<[string, string]> = [
      [`import App from './App'`, './App'],                                          // no semicolon
      [`import App from "./App";`, './App'],                                         // double quotes
      [`import {\n  PipelinePage,\n} from './crm/PipelinePage';`, './crm/PipelinePage'], // wrapped
      [`export { PipelinePage } from './crm/PipelinePage';`, './crm/PipelinePage'],    // re-export
      [`import './App';`, './App'],                                                  // side effect only
      [`import React from 'react'; import App from './App';`, './App'],               // two on one line
      [`import{default as App}from'./App'`, './App'],                                // no whitespace
      [`import * as Everything from './App';`, './App'],                             // namespace
    ];
    expect(eagerShapes.length).toBe(8);
    for (const [shape, expected] of eagerShapes) {
      expect(staticImportSpecifiers(shape), shape).toContain(expected);
    }

    // …and these must stay invisible to the STATIC scanner, or every assertion above is vacuous.
    expect(staticImportSpecifiers(`const A = lazy(() => import('./App'));`)).toEqual([]);
    expect(staticImportSpecifiers(`import type { Foo } from './App';`)).toEqual([]);
    expect(staticImportSpecifiers(`export type { Foo } from './App';`)).toEqual([]);

    // …while the DYNAMIC scanner sees exactly them, in both spellings App.tsx uses.
    expect(dynamicImportSpecifiers(`const A = lazy(() => import('./App'));`)).toEqual(['./App']);
    expect(dynamicImportSpecifiers(`const P = lazy(() =>\n  import('./x/Page').then((m) => ({ default: m.Page })));`)).toEqual(['./x/Page']);
    expect(dynamicImportSpecifiers(`import App from './App';`)).toEqual([]);
  });

  it('resolves specifiers the way the bundler does, and fails loudly when it cannot', () => {
    expect(resolve('./App.tsx', './crm/gtd/pages')).toBe('./crm/gtd/pages.ts');
    expect(resolve('./crm/gtd/InboxPage.tsx', '../../shared/search')).toBe('./shared/search/index.ts');
    expect(resolve('./crm/gtd/PublicTodoApp.tsx', './publicMode')).toBe('./crm/gtd/publicMode.ts');
    expect(resolve('./main.tsx', './Root.tsx')).toBe('./Root.tsx');
    // "Can't tell" is a failure, never a module silently dropped from the sweep.
    expect(() => resolve('./App.tsx', './does/not/exist')).toThrow(/cannot resolve/);
    expect(() => read('./nope.tsx')).toThrow(/no source module/);
  });

  it('tells a nested JSX element from a merely later sibling', () => {
    // The distinction the Root assertion rests on. Pinned in BOTH directions, because a
    // detector that answered `true` for the sibling case would make that assertion an
    // expensive way of restating source order.
    const nested = `const R = () => (<Boundary><Suspense>{x}</Suspense></Boundary>);`;
    const deep = `const R = () => (<Boundary><div><p><Suspense /></p></div></Boundary>);`;
    const sibling = `const R = () => (<><Boundary>{a}</Boundary><Suspense>{b}</Suspense></>);`;
    const before = `const R = () => (<><Suspense>{b}</Suspense><Boundary>{a}</Boundary></>);`;
    expect(jsxNests(nested, 'Boundary', 'Suspense')).toBe(true);
    expect(jsxNests(deep, 'Boundary', 'Suspense')).toBe(true);
    expect(jsxNests(sibling, 'Boundary', 'Suspense')).toBe(false);
    expect(jsxNests(before, 'Boundary', 'Suspense')).toBe(false);
  });

  it('finds lazy() calls through an alias, and knows module scope from a component body', () => {
    // Pins `lazyCallSites` in both directions. A text scan gets each of these wrong: the
    // aliased call is invisible to it, and the comment is a false positive.
    const moduleScope = `import { lazy } from 'react';\nconst A = lazy(() => import('./A'));`;
    const aliased = `import { lazy as makeLazy } from 'react';\nfunction C() { const A = makeLazy(() => import('./A')); return <A />; }`;
    const insideBody = `import { lazy } from 'react';\nexport default function App() { const A = lazy(() => import('./A')); return <A />; }`;
    const commentOnly = `import { lazy } from 'react';\nconst A = lazy(() => import('./A'));\nfunction App() { /* never call lazy( here */ return null; }`;
    const notReact = `import { lazy } from 'other';\nfunction C() { return lazy(() => 1); }`;

    expect(lazyCallSites(moduleScope).map((s) => s.insideFunction)).toEqual([false]);
    expect(lazyCallSites(aliased).map((s) => s.insideFunction)).toEqual([true]);
    expect(lazyCallSites(insideBody).map((s) => s.insideFunction)).toEqual([true]);
    // The lazy() argument IS an arrow function; descending into it must not report the call
    // that owns it as nested, or every correct file would fail.
    expect(lazyCallSites(commentOnly).map((s) => s.insideFunction)).toEqual([false]);
    // A `lazy` from somewhere else is not React's.
    expect(lazyCallSites(notReact)).toEqual([]);

    // The evasions an import-name-only detector misses. The first was MEASURED against the
    // real App.tsx before this branch existed: the guard stayed green while a lazy() inside the
    // component body remounted the whole route subtree on every render.
    const reAliased = `import { lazy } from 'react';\nconst mkLazy = lazy;\nfunction App() { const A = mkLazy(() => import('./A')); return <A />; }`;
    const chained = `import { lazy } from 'react';\nconst a = lazy;\nconst b = a;\nfunction App() { const A = b(() => import('./A')); return <A />; }`;
    const namespaced = `import * as React from 'react';\nfunction App() { const A = React.lazy(() => import('./A')); return <A />; }`;
    const namespaceAlias = `import React from 'react';\nconst mk = React.lazy;\nfunction App() { const A = mk(() => import('./A')); return <A />; }`;
    expect(lazyCallSites(reAliased).map((s) => s.insideFunction)).toEqual([true]);
    expect(lazyCallSites(chained).map((s) => s.insideFunction)).toEqual([true]);
    expect(lazyCallSites(namespaced).map((s) => s.insideFunction)).toEqual([true]);
    expect(lazyCallSites(namespaceAlias).map((s) => s.insideFunction)).toEqual([true]);

    // The shortest evasion of all, and the one a module-scope-only alias sweep missed: the
    // alias declared inside the component, right next to the call it enables.
    const localAlias = `import { lazy } from 'react';\nfunction App() { const mk = lazy; const A = mk(() => import('./A')); return <A />; }`;
    const localChained = `import { lazy } from 'react';\nfunction App() { const a = lazy; const b = a; const A = b(() => import('./A')); return <A />; }`;
    expect(lazyCallSites(localAlias).map((s) => s.insideFunction)).toEqual([true]);
    expect(lazyCallSites(localChained).map((s) => s.insideFunction)).toEqual([true]);

    // The destructured form. `const { lazy } = React` binds through an ObjectBindingPattern,
    // which an identifier-only alias check skips outright — so the binding stays unknown and
    // every call through it is invisible to the module-scope rule.
    const destructured = `import * as React from 'react';\nconst { lazy } = React;\nfunction App() { const A = lazy(() => import('./A')); return <A />; }`;
    const destructuredRenamed = `import React from 'react';\nfunction App() { const { lazy: mk } = React; const A = mk(() => import('./A')); return <A />; }`;
    expect(lazyCallSites(destructured).map((s) => s.insideFunction)).toEqual([true]);
    expect(lazyCallSites(destructuredRenamed).map((s) => s.insideFunction)).toEqual([true]);
  });

  it('sees import.meta.glob in every spelling that bundles a directory', () => {
    // `import.meta.glob` is the one construct that can pull a whole directory into a chunk
    // with no specifier to allowlist — and this test file uses it itself, which is exactly why
    // the detector must not be a text match for the literal call.
    const plain = `const m = import.meta.glob('./crm/**/*.tsx', { eager: true });`;
    const arrayArg = `const m = import.meta.glob(['./a/*.ts', '!./a/*.test.ts'], { query: '?raw' });`;
    const wrapped = `const m = import.meta\n  .glob('./crm/**/*.tsx');`;
    const computed = `const m = import.meta.glob(PATTERN);`;
    const unrelated = `const m = shelf.glob('./a/*.ts'); const n = { glob: 1 };`;

    expect(importMetaGlobPatterns(plain)).toEqual(['./crm/**/*.tsx']);
    expect(importMetaGlobPatterns(arrayArg)).toEqual(['./a/*.ts , !./a/*.test.ts']);
    expect(importMetaGlobPatterns(wrapped)).toEqual(['./crm/**/*.tsx']);
    // A runtime-built pattern is reported, never silently invisible.
    expect(importMetaGlobPatterns(computed)).toEqual(['<computed>']);
    expect(importMetaGlobPatterns(unrelated)).toEqual([]);

    // Aliased, the same evasion `lazyCallSites` already resolves. A callee-shape-only matcher
    // records nothing here, and "nothing" is what makes `expect(globs).toEqual([])` pass while
    // a whole directory is bundled statically into the /todo download.
    const aliased = `const g = import.meta.glob;\nconst m = g('../components/*.tsx', { eager: true });`;
    expect(importMetaGlobPatterns(aliased)).toEqual(['../components/*.tsx']);
    // …but an unrelated local function called `g` is not a glob.
    expect(importMetaGlobPatterns(`const g = other.thing;\nconst m = g('./x');`)).toEqual([]);
  });

  it('reads a dynamic import written any way it can be written, and fails closed on the rest', () => {
    // The public-graph assertion is `dynamic` must be EMPTY, so a specifier this scanner cannot
    // see is a specifier that graph is not protected against. Recording only plain string
    // literals meant a backtick import produced no entry at all and slipped straight through.
    expect(dynamicImportSpecifiers("const a = import('./A');")).toEqual(['./A']);
    expect(dynamicImportSpecifiers('const a = import("./A");')).toEqual(['./A']);
    expect(dynamicImportSpecifiers('const a = import(`./A`);')).toEqual(['./A']);
    expect(dynamicImportSpecifiers('void import(`../../crm/CrmLayout`);')).toEqual(['../../crm/CrmLayout']);
    // Unreadable statically → reported as computed, never omitted.
    expect(dynamicImportSpecifiers('const a = import(PATH);')).toEqual(['<computed>']);
    expect(dynamicImportSpecifiers('const a = import(`./pages/${name}`);')).toEqual(['<computed>']);
    // A STATIC import is not a dynamic one, or every "dynamic must be empty" assertion inverts.
    expect(dynamicImportSpecifiers("import A from './A';")).toEqual([]);
  });

  it('the closure reports dynamic imports and globs without following them', () => {
    // `staticClosure` must not FOLLOW a dynamic edge (that edge is the boundary under test)
    // but must REPORT it, or a `void import('../../CrmLayout')` inside a GTD page reads as a
    // clean graph. Pinned against the real App.tsx, whose lazy route table is exactly that
    // shape: nineteen-plus dynamic edges, none of them followed into the closure.
    const { modules, dynamic } = staticClosure('./App.tsx');
    expect(dynamic.length).toBeGreaterThanOrEqual(20);

    // Reported from EVERY module the closure reaches, not just the root — which is the whole
    // point: `TodosModeRouter` is an eager leaf of App, and its own `lazy(() => import(
    // '../TodosPage'))` is exactly the shape a CRM import hidden one hop down would take.
    const importers = new Set(dynamic.map(([importer]) => importer));
    expect(importers).toContain('./App.tsx');
    expect(importers).toContain('./crm/gtd/TodosModeRouter.tsx');

    // …and every target stayed OUT of the closure, which is what "not followed" means.
    expect(modules).not.toContain('./crm/PipelinePage.tsx');
    expect(modules).not.toContain('./crm/gtd/pages.ts');
    expect(modules).not.toContain('./crm/TodosPage.tsx');
  });
});
